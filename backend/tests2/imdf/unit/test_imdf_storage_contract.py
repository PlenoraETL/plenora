"""
Contract test — app/core/imdf/storage.py (lazy cache S3).

Copre:
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.imdf, pytest.mark.contract]


class _FakeStorage:
    def __init__(self):
        self.objects: dict = {}  # key -> bytes
        self.upload_calls = []
        self.delete_calls = []

    def list_objects(self, prefix):
        return [
            {"key": k, "last_modified": None, "size": len(v)}
            for k, v in self.objects.items()
            if k.startswith(prefix)
        ]

    def upload(self, local_path, key):
        self.upload_calls.append((str(local_path), key))
        self.objects[key] = Path(local_path).read_bytes()

    def upload_bytes(self, data, key):
        self.upload_calls.append(("<bytes>", key))
        self.objects[key] = bytes(data)

    def download(self, key, local_path):
        if key not in self.objects:
            raise RuntimeError(f"NoSuchKey: {key}")
        Path(local_path).write_bytes(self.objects[key])

    def get_object_stream(self, key):
        if key not in self.objects:
            raise RuntimeError(f"NoSuchKey: {key}")
        data = self.objects[key]
        return {"body": iter([data]), "size": len(data), "content_type": "application/json"}

    def delete(self, key):
        self.delete_calls.append(key)
        self.objects.pop(key, None)


@pytest.fixture
def fake_storage(monkeypatch):
    fake = _FakeStorage()
    from app.core.imdf import storage as storage_mod
    monkeypatch.setattr(storage_mod, "_storage", lambda: fake)
    return fake


@pytest.fixture
def fake_settings(monkeypatch, tmp_path):
    # Patch IMDF_BUNDLES_DIR su un tmp path, cosi' non impattiamo prod.
    from app.core.imdf import storage as storage_mod
    bundles_root = tmp_path / "bundles"
    bundles_root.mkdir()
    class _FakeSettings:
        IMDF_BUNDLES_DIR = bundles_root
    monkeypatch.setattr("app.config.settings", _FakeSettings, raising=False)
    # Ricompila _local_dir che importa settings lazy
    monkeypatch.setattr(
        storage_mod, "_local_dir",
        lambda task_id: bundles_root / task_id,
    )
    return bundles_root


def _write_bundle(dir_path: Path, site_id: str = "SITE01"):
    """Crea una struttura bundle minima per i test."""
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "plenora_metadata.json").write_text(
        json.dumps({"project_id": "proj-1", "site_id": site_id, "task_id": "t1"}),
        encoding="utf-8",
    )
    (dir_path / "validation.json").write_text(
        json.dumps({"passed": True, "total_errors": 0}), encoding="utf-8"
    )
    site_dir = dir_path / site_id
    site_dir.mkdir()
    (site_dir / "building.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8"
    )


# Tombstone (IMDF-4 step 4): il legacy `save_bundle(task_id, local_dir,
# meta)` zip-based e' stato rimosso. Sostituito da `save_bundle_files`
# (writer RAM-first IMDF-3 introdotto in persist_imdf_bundle).


def test_save_bundle_legacy_tombstoned():
    """IMDF-4 step 4: tentativo di chiamare `save_bundle` legacy ->
    AttributeError (function rimossa). Anti-regression."""
    from app.core.imdf import storage as st

    assert not hasattr(st, "save_bundle"), (
        "save_bundle legacy deve essere rimosso post-IMDF-4 step 4 "
        "(writer zip-based pre-IMDF-3). Usa save_bundle_files (RAM-first)."
    )


def _seed_files_layout(fake_storage, task_id: str, meta: dict, files: dict[str, bytes]):
    """Helper: popola il fake_storage col layout IMDF-3 (files/* +
    meta.json) per simulare un bundle persisted via save_bundle_files."""
    import json as _json
    fake_storage.objects[f"imdf-bundles/{task_id}.meta.json"] = (
        _json.dumps(meta).encode("utf-8")
    )
    for name, data in files.items():
        fake_storage.objects[f"imdf-bundles/{task_id}/files/{name}"] = data


# ensure_local_bundle — TOMBSTONED post QUERY-1 step 3


def test_ensure_local_bundle_tombstoned():
    """`storage.ensure_local_bundle` rimosso post-QUERY-1 step 3.
    Endpoint IMDF leggono da storage direttamente (read_manifest /
    read_validation / read_renderers / list_bundle_file_entries /
    open_gdb_in_vsimem)."""
    from app.core.imdf import storage as st
    assert not hasattr(st, "ensure_local_bundle"), (
        "ensure_local_bundle rimosso post-QUERY-1 step 3."
    )


# get_meta_only


def test_get_meta_only_reads_without_downloading_zip(fake_storage, fake_settings, tmp_path):
    from app.core.imdf import storage as st

    _seed_files_layout(
        fake_storage, "task-1",
        meta={"project_id": "proj-1", "site_id": "SITE01"},
        files={},
    )

    meta = st.get_meta_only("task-1")
    assert meta is not None
    assert meta["project_id"] == "proj-1"
    assert meta["site_id"] == "SITE01"


def test_get_meta_only_missing_returns_none(fake_storage, fake_settings):
    from app.core.imdf import storage as st

    assert st.get_meta_only("task-missing") is None


# list_task_ids


def test_list_task_ids_returns_only_meta(fake_storage, fake_settings, tmp_path):
    """Il listing conta ogni task UNA volta tramite il meta.json key."""
    from app.core.imdf import storage as st

    _seed_files_layout(
        fake_storage, "task-a",
        meta={"project_id": "proj-1", "site_id": "S1"},
        files={},
    )
    _seed_files_layout(
        fake_storage, "task-b",
        meta={"project_id": "proj-1", "site_id": "S2"},
        files={},
    )

    ids = st.list_task_ids()
    assert sorted(ids) == ["task-a", "task-b"]


def test_list_task_ids_empty_when_no_bundles(fake_storage, fake_settings):
    from app.core.imdf import storage as st

    assert st.list_task_ids() == []


# delete_bundle


def test_delete_bundle_removes_s3_and_local(fake_storage, fake_settings, tmp_path):
    """IMDF-3: delete_bundle elimina meta + gdb zip + tutti
    i file individuali sotto `{task_id}/files/`. Count = numero di
    delete chiamati."""
    from app.core.imdf import storage as st

    _seed_files_layout(
        fake_storage, "task-1",
        meta={"project_id": "proj-1", "site_id": "S1"},
        files={
            "plenora_metadata.json": b"{}",
            "S1/building.geojson": b"{}",
        },
    )
    # Warm cache locale
    local = fake_settings / "task-1"
    _write_bundle(local)

    count = st.delete_bundle("task-1")
    # Fixed keys (meta + gdb zip) sempre tentati = 2,
    # piu' i 2 file individuali = 4.
    assert count == 4
    assert "imdf-bundles/task-1.meta.json" not in fake_storage.objects
    assert "imdf-bundles/task-1/files/plenora_metadata.json" not in fake_storage.objects
    assert "imdf-bundles/task-1/files/S1/building.geojson" not in fake_storage.objects
    assert not local.exists()


def test_delete_bundle_idempotent_when_nothing(fake_storage, fake_settings):
    """delete su task inesistente non fallisce (storage delete e' idempotente).
    IMDF-5: count riflette i tentativi sui key fissi (meta + gdb zip)."""
    from app.core.imdf import storage as st

    count = st.delete_bundle("task-nope")
    assert count == 2


def test_delete_bundle_can_skip_local_cleanup(fake_storage, fake_settings, tmp_path):
    from app.core.imdf import storage as st

    _seed_files_layout(
        fake_storage, "task-1",
        meta={"project_id": "proj-1", "site_id": "S1"},
        files={},
    )
    local = fake_settings / "task-1"
    _write_bundle(local)

    count = st.delete_bundle("task-1", cleanup_local=False)

    # 2 fixed keys + 0 files (questo seed non ha files individuali).
    assert count == 2
    assert local.exists()
