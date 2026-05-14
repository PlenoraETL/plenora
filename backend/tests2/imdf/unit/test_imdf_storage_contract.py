"""
Contract test — app/core/imdf/storage.py (lazy cache S3).

Copre:
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
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


# save_bundle


def test_save_bundle_uploads_zip_and_meta(fake_storage, fake_settings, tmp_path):
    from app.core.imdf import storage as st

    local = tmp_path / "mybundle"
    _write_bundle(local)
    result = st.save_bundle("task-1", local)

    assert result["zip_key"] == "imdf-bundles/task-1.zip"
    assert result["meta_key"] == "imdf-bundles/task-1.meta.json"
    assert "imdf-bundles/task-1.zip" in fake_storage.objects
    assert "imdf-bundles/task-1.meta.json" in fake_storage.objects

    # Verifica che il zip contenga il file (check estraibilita')
    zip_bytes = fake_storage.objects["imdf-bundles/task-1.zip"]
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "plenora_metadata.json" in names
        assert any(n.endswith("building.geojson") for n in names)

    # Verifica meta
    meta_bytes = fake_storage.objects["imdf-bundles/task-1.meta.json"]
    meta = json.loads(meta_bytes.decode("utf-8"))
    assert meta["project_id"] == "proj-1"


def test_save_bundle_rejects_missing_dir(fake_storage, fake_settings, tmp_path):
    from app.core.imdf import storage as st

    missing = tmp_path / "nope"
    with pytest.raises(FileNotFoundError):
        st.save_bundle("task-1", missing)


# ensure_local_bundle


def test_ensure_local_bundle_cache_hit(fake_storage, fake_settings, tmp_path):
    """Se local esiste e ha contenuto, no download da S3."""
    from app.core.imdf import storage as st

    local = fake_settings / "task-1"
    _write_bundle(local)

    # fake_storage.objects e' vuoto: se tentasse download, il test fallirebbe
    result = st.ensure_local_bundle("task-1")
    assert result == local
    assert not fake_storage.upload_calls  # nessun I/O su storage


def test_ensure_local_bundle_cache_miss_downloads(fake_storage, fake_settings, tmp_path):
    """Cache miss: scarica zip da S3, extract, ritorna local path."""
    from app.core.imdf import storage as st

    # Prepara un zip su fake storage (simula bundle generato da altro nodo)
    source = tmp_path / "source_bundle"
    _write_bundle(source)
    st.save_bundle("task-1", source)

    # Wipe cache locale
    import shutil
    shutil.rmtree(fake_settings / "task-1", ignore_errors=True)
    assert not (fake_settings / "task-1").exists()

    # Ensure -> deve scaricare
    result = st.ensure_local_bundle("task-1")
    assert result.exists()
    assert (result / "plenora_metadata.json").exists()
    assert (result / "SITE01" / "building.geojson").exists()


def test_ensure_local_bundle_not_found_raises(fake_storage, fake_settings):
    from app.core.imdf import storage as st

    with pytest.raises(FileNotFoundError):
        st.ensure_local_bundle("task-does-not-exist")


# get_meta_only


def test_get_meta_only_reads_without_downloading_zip(fake_storage, fake_settings, tmp_path):
    from app.core.imdf import storage as st

    source = tmp_path / "source"
    _write_bundle(source)
    st.save_bundle("task-1", source)

    meta = st.get_meta_only("task-1")
    assert meta is not None
    assert meta["project_id"] == "proj-1"
    assert meta["site_id"] == "SITE01"


def test_get_meta_only_missing_returns_none(fake_storage, fake_settings):
    from app.core.imdf import storage as st

    assert st.get_meta_only("task-missing") is None


# list_task_ids


def test_list_task_ids_returns_only_meta(fake_storage, fake_settings, tmp_path):
    """Il listing conta ogni task UNA volta, non 2 (zip + meta)."""
    from app.core.imdf import storage as st

    source1 = tmp_path / "s1"
    source2 = tmp_path / "s2"
    _write_bundle(source1)
    _write_bundle(source2, site_id="SITE02")
    st.save_bundle("task-a", source1)
    st.save_bundle("task-b", source2)

    ids = st.list_task_ids()
    assert sorted(ids) == ["task-a", "task-b"]


def test_list_task_ids_empty_when_no_bundles(fake_storage, fake_settings):
    from app.core.imdf import storage as st

    assert st.list_task_ids() == []


# delete_bundle


def test_delete_bundle_removes_s3_and_local(fake_storage, fake_settings, tmp_path):
    from app.core.imdf import storage as st

    source = tmp_path / "s1"
    _write_bundle(source)
    st.save_bundle("task-1", source)
    # Warm cache locale
    local = fake_settings / "task-1"
    _write_bundle(local)

    count = st.delete_bundle("task-1")
    assert count == 2  # zip + meta
    assert "imdf-bundles/task-1.zip" not in fake_storage.objects
    assert "imdf-bundles/task-1.meta.json" not in fake_storage.objects
    assert not local.exists()


def test_delete_bundle_idempotent_when_nothing(fake_storage, fake_settings):
    """delete su task inesistente non fallisce (S3 delete e' idempotente)."""
    from app.core.imdf import storage as st

    count = st.delete_bundle("task-nope")
    # Anche se 0 key S3, il count riflette i tentativi (2 = zip + meta tentati)
    assert count == 2


def test_delete_bundle_can_skip_local_cleanup(fake_storage, fake_settings, tmp_path):
    from app.core.imdf import storage as st

    source = tmp_path / "s1"
    _write_bundle(source)
    st.save_bundle("task-1", source)
    local = fake_settings / "task-1"
    _write_bundle(local)

    count = st.delete_bundle("task-1", cleanup_local=False)

    assert count == 2
    assert local.exists()
