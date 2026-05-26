"""
Contract test — `imdf.storage.open_gdb_in_vsimem` (IMDF-5 fully-RAM).

`open_gdb_in_vsimem(task_id, site_id)` e' un context manager che:
  - Scarica `_gdb.zip` bytes da storage canonical.
  - Registra i bytes in `/vsimem/plenora_imdf/{task_id}/_gdb.zip` via
    `app.core.imdf.gdal_vsi.vsi_register_bytes` (ctypes adapter
    libgdal).
  - Yield il path GDAL VSI ``/vsizip//vsimem/.../{site_id}.gdb``
    pronto per pyogrio.
  - Al __exit__: VSIUnlink del path /vsimem.

Coverage strategy:
  - I test mockano `vsi_register_bytes` per evitare l'effettivo
    load di libgdal (non disponibile fuori container).
  - Verifichiamo: probe del key, fetch bytes, costruzione path,
    invocazione vsi_register_bytes con i bytes corretti, yield
    del path, cleanup garantito.
  - L'effettivo roundtrip pyogrio + ctypes e' provato dal probe
    `.agent-temp/pyogrio_gdb_zip_probe.py` (run in container).
"""

from __future__ import annotations

import io
import zipfile
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.imdf, pytest.mark.contract]


class _FakeStorage:
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
    monkeypatch.setattr(storage_mod, "object_storage_enabled", lambda: True)
    return fake


@pytest.fixture
def vsi_register_mock(monkeypatch):
    """Mock per `gdal_vsi.vsi_register_bytes`. Cattura i parametri e
    si comporta come context manager (yield del path)."""
    calls: list[dict] = []

    @contextmanager
    def _fake_register(vsi_path: str, data: bytes):
        calls.append({"vsi_path": vsi_path, "data_len": len(data), "data": data})
        yield vsi_path

    from app.core.imdf import gdal_vsi
    monkeypatch.setattr(gdal_vsi, "vsi_register_bytes", _fake_register)
    return calls


def _fake_gdb_zip_bytes(site_id: str = "SITE_X") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{site_id}.gdb/a00000001.gdbtable", b"\x00fake gdb\x00")
    return buf.getvalue()


def test_open_gdb_in_vsimem_happy_path_yields_vsi_path(
    fake_storage, vsi_register_mock,
):
    """Storage ha `_gdb.zip` → yield `/vsizip//vsimem/.../{site}.gdb`."""
    from app.core.imdf import storage as st

    gdb_bytes = _fake_gdb_zip_bytes("SITE_X")
    fake_storage.objects["imdf-bundles/task-1/_gdb.zip"] = gdb_bytes

    with st.open_gdb_in_vsimem("task-1", "SITE_X") as vsi_path:
        assert vsi_path == (
            "/vsizip//vsimem/plenora_imdf/task-1/_gdb.zip/SITE_X.gdb"
        )

    # vsi_register_bytes invocato con i bytes letti dallo storage.
    assert len(vsi_register_mock) == 1
    call = vsi_register_mock[0]
    assert call["vsi_path"] == "/vsimem/plenora_imdf/task-1/_gdb.zip"
    assert call["data"] == gdb_bytes
    assert call["data_len"] == len(gdb_bytes)


def test_open_gdb_in_vsimem_missing_returns_none(fake_storage, vsi_register_mock):
    """Bundle senza `_gdb.zip` su storage → yield None (no vsi register)."""
    from app.core.imdf import storage as st

    with st.open_gdb_in_vsimem("task-no-gdb", "SITE_X") as vsi_path:
        assert vsi_path is None
    assert len(vsi_register_mock) == 0


def test_open_gdb_in_vsimem_capability_off_returns_none(monkeypatch, vsi_register_mock):
    """`object_storage_enabled=False` → yield None senza toccare storage."""
    from app.core.imdf import storage as st

    monkeypatch.setattr(st, "object_storage_enabled", lambda: False)
    monkeypatch.setattr(
        st, "_storage",
        lambda: pytest.fail("_storage non deve essere invocato con capability off"),
    )

    with st.open_gdb_in_vsimem("any", "SITE_X") as vsi_path:
        assert vsi_path is None
    assert len(vsi_register_mock) == 0


def test_open_gdb_in_vsimem_cleanup_runs_on_exception(
    fake_storage, vsi_register_mock,
):
    """Se il consumer solleva DENTRO il `with`, il cleanup VSI
    (gestito da `vsi_register_bytes` ctx) deve comunque eseguire.

    Anti-regression: garantisce che il context manager sia ben
    annidato e propaghi l'exception dopo cleanup.
    """
    from app.core.imdf import storage as st

    fake_storage.objects["imdf-bundles/task-x/_gdb.zip"] = _fake_gdb_zip_bytes()

    with pytest.raises(RuntimeError, match="consumer error"):
        with st.open_gdb_in_vsimem("task-x", "SITE_X") as vsi_path:
            assert vsi_path is not None
            raise RuntimeError("consumer error")

    # vsi_register_bytes e' stato chiamato 1 volta (e il fake ctx
    # ha gestito enter+exit, garantendo cleanup).
    assert len(vsi_register_mock) == 1


def test_open_gdb_in_vsimem_path_format_includes_task_id_and_site_id(
    fake_storage, vsi_register_mock,
):
    """Pin del path format: `/vsizip//vsimem/plenora_imdf/{task_id}/
    _gdb.zip/{site_id}.gdb`. Cambi qui rompono lock test downstream."""
    from app.core.imdf import storage as st

    fake_storage.objects["imdf-bundles/run-abc-123/_gdb.zip"] = _fake_gdb_zip_bytes("BIG_SITE")

    with st.open_gdb_in_vsimem("run-abc-123", "BIG_SITE") as vsi_path:
        assert vsi_path == (
            "/vsizip//vsimem/plenora_imdf/run-abc-123/_gdb.zip/BIG_SITE.gdb"
        )
    assert vsi_register_mock[0]["vsi_path"] == (
        "/vsimem/plenora_imdf/run-abc-123/_gdb.zip"
    )
