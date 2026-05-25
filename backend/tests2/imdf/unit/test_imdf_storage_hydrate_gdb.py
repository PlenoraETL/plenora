"""
Contract test — `imdf.storage.hydrate_gdb_to_temp` (IMDF-4 step 3).

`hydrate_gdb_to_temp` scarica `_gdb.zip` da storage in una temp dir
scoped al call, ed estrae la `.gdb/` directory pronta per pyogrio.
Restituisce `GdbHandle` con `gdb_path` + `cleanup()`. None se il
bundle non ha .gdb su storage.

Coverage:
  - happy path: gdb zip presente -> extract + Path .gdb/ ritornato
  - missing: no _gdb.zip key -> None
  - cleanup: handle.cleanup() rimuove la temp dir
  - context manager: `with hydrate_gdb_to_temp(...)` cleans up
  - extract failed: temp dir ripulita su exception
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.imdf, pytest.mark.contract]


class _FakeStorage:
    """Fake S3 minimo per hydrate_gdb_to_temp."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def list_objects(self, prefix):
        return [
            {"key": k, "last_modified": None, "size": len(v)}
            for k, v in self.objects.items()
            if k.startswith(prefix) or k == prefix
        ]

    def upload_bytes(self, data, key):
        self.objects[key] = bytes(data)

    def get_object_stream(self, key):
        if key not in self.objects:
            raise RuntimeError(f"NoSuchKey: {key}")
        data = self.objects[key]
        return {"body": iter([data]), "size": len(data), "content_type": "application/zip"}

    def delete(self, key):
        self.objects.pop(key, None)


@pytest.fixture
def fake_storage(monkeypatch):
    fake = _FakeStorage()
    from app.core.imdf import storage as storage_mod
    monkeypatch.setattr(storage_mod, "_storage", lambda: fake)
    monkeypatch.setattr(
        storage_mod, "object_storage_enabled", lambda: True,
    )
    return fake


def _build_fake_gdb_zip(site_id: str = "SITE_X") -> bytes:
    """Crea uno zip con un .gdb dir popolata da file dummy.
    Mirror del layout prodotto da `_zip_gdb_dir_to_bytes`.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{site_id}.gdb/a00000001.gdbtable", b"\x00fake gdb\x00")
        zf.writestr(f"{site_id}.gdb/a00000001.gdbtablx", b"\x00idx\x00")
    return buf.getvalue()


def test_hydrate_gdb_to_temp_happy_path(fake_storage):
    """Storage ha _gdb.zip -> hydrate ritorna GdbHandle con gdb_path
    valido + temp_root cleanupable."""
    from app.core.imdf import storage as st

    gdb_zip = _build_fake_gdb_zip("SITE_X")
    fake_storage.objects["imdf-bundles/task-1/_gdb.zip"] = gdb_zip

    handle = st.hydrate_gdb_to_temp("task-1")
    try:
        assert handle is not None
        assert handle.gdb_path.is_dir()
        assert handle.gdb_path.name == "SITE_X.gdb"
        # Tutti i file dummy estratti.
        assert (handle.gdb_path / "a00000001.gdbtable").exists()
        assert (handle.gdb_path / "a00000001.gdbtablx").exists()
        # temp_root contiene la .gdb come unico top-level.
        assert handle.gdb_path.parent == handle.temp_root
    finally:
        handle.cleanup()
        assert not handle.temp_root.exists(), (
            "cleanup() deve rimuovere la temp dir"
        )


def test_hydrate_gdb_to_temp_missing_returns_none(fake_storage):
    """Bundle senza _gdb.zip su storage -> None (no temp dir creata)."""
    from app.core.imdf import storage as st

    assert st.hydrate_gdb_to_temp("task-no-gdb") is None


def test_hydrate_gdb_to_temp_returns_none_when_object_storage_off(monkeypatch):
    """Capability object_storage off -> None (no storage call)."""
    from app.core.imdf import storage as st

    monkeypatch.setattr(st, "object_storage_enabled", lambda: False)
    # Anche se patchassimo _storage, non dovrebbe essere chiamato.
    monkeypatch.setattr(
        st, "_storage",
        lambda: pytest.fail("_storage non deve essere invocato con capability off"),
    )

    assert st.hydrate_gdb_to_temp("any") is None


def test_hydrate_gdb_to_temp_context_manager_cleanup(fake_storage):
    """`with hydrate_gdb_to_temp(...) as h` cleans up at exit."""
    from app.core.imdf import storage as st

    fake_storage.objects["imdf-bundles/task-1/_gdb.zip"] = _build_fake_gdb_zip()
    handle = st.hydrate_gdb_to_temp("task-1")
    assert handle is not None
    temp_root = handle.temp_root
    with handle as h:
        assert h.gdb_path.is_dir()
        assert temp_root.exists()
    # Context exit -> cleanup eseguito.
    assert not temp_root.exists()


def test_hydrate_gdb_to_temp_invalid_zip_cleans_up(fake_storage, monkeypatch):
    """Se il payload non e' uno zip valido, hydrate solleva e ripulisce
    la temp dir creata (no leak)."""
    from app.core.imdf import storage as st

    fake_storage.objects["imdf-bundles/task-bad/_gdb.zip"] = b"not-a-zip"

    # Tracciamo mkdtemp per verificare il cleanup.
    created_dirs: list[Path] = []
    real_mkdtemp = st.tempfile.mkdtemp

    def _tracked_mkdtemp(prefix=""):
        d = real_mkdtemp(prefix=prefix)
        created_dirs.append(Path(d))
        return d

    monkeypatch.setattr(st.tempfile, "mkdtemp", _tracked_mkdtemp)

    with pytest.raises(Exception):
        st.hydrate_gdb_to_temp("task-bad")

    assert len(created_dirs) == 1
    assert not created_dirs[0].exists(), (
        "temp dir creata deve essere ripulita su zipfile error"
    )


def test_hydrate_gdb_to_temp_zip_without_gdb_returns_none(fake_storage):
    """Zip valido ma senza una .gdb dir top-level -> None + cleanup."""
    from app.core.imdf import storage as st

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("random/file.txt", b"noise")
    fake_storage.objects["imdf-bundles/task-w/_gdb.zip"] = buf.getvalue()

    handle = st.hydrate_gdb_to_temp("task-w")
    assert handle is None
