import logging

import pytest

from app.db import results_db_service


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


class _LogCapturer(logging.Handler):
    """Handler locale: caplog non raccoglie log del namespace `app.*`
    perche' `app.utils.logger.get_logger` setta propagate=False all'init."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def _attach_logger():
    capturer = _LogCapturer()
    # BE-GO-READY-RESOURCE-ENGINE-002: il PERF log e' emesso ora dal
    # modulo loader (che possiede la dispatch consolidata). Il
    # capturer si attacca al logger del loader; il public surface di
    # `get_result_data` e' invariato, sono solo cambiate le coordinate
    # del logger interno.
    capturer = _LogCapturer()
    loader_logger = logging.getLogger(
        "app.core.resources.loaders.result_blob"
    )
    loader_logger.addHandler(capturer)
    return loader_logger, capturer


def test_get_result_data_converts_memoryview_to_bytes(monkeypatch):
    # BE-GO-READY-RESOURCE-ENGINE-002: SQL fetch happens inside the
    # loader's `_fetch_row`, which lazily imports
    # `app.db.pg_connection.fetchone`. Patching at that boundary is
    # the canonical replacement for the pre-pilot
    # `monkeypatch.setattr(results_db_service, "fetchone", ...)`.
    import app.db.pg_connection as pg_conn

    monkeypatch.setattr(
        pg_conn,
        "fetchone",
        lambda sql, params=(): {"data": memoryview(b"abc"), "storage_key": None},
    )

    data = results_db_service.get_result_data("res-1")

    assert data == b"abc"


def test_get_result_data_uses_group_filter_when_provided(monkeypatch):
    import app.db.pg_connection as pg_conn

    seen = {}

    def _fetchone(sql, params=()):
        seen["sql"] = sql
        seen["params"] = params
        return {"data": b"abc", "storage_key": None}

    monkeypatch.setattr(pg_conn, "fetchone", _fetchone)

    results_db_service.get_result_data("res-1", group_id="grp-1")

    assert "group_id = ?" in seen["sql"]
    assert seen["params"] == ("res-1", "grp-1")


# ==============================================================================
# A1 batch — log naming contract: backend=object_storage / backend=db_legacy
# ==============================================================================


def test_get_result_data_logs_backend_db_legacy_for_bytea_row(monkeypatch):
    """Row con storage_key=NULL e data BYTEA: il log marker deve riportare
    `backend=db_legacy` (non piu' `backend=db`). Distinzione semantica:
    i nuovi result in policy=object_storage non scrivono mai BYTEA, quindi
    qualunque hit su questo path e' su row legacy pre-migration.

    BE-GO-READY-RESOURCE-ENGINE-002: il log e' ora emesso dal loader;
    il marker e il formato sono preservati verbatim dal pilot.
    """
    import app.db.pg_connection as pg_conn

    monkeypatch.setattr(
        pg_conn,
        "fetchone",
        lambda sql, params=(): {"data": b"legacy bytes", "storage_key": None},
    )

    mod_logger, capturer = _attach_logger()
    try:
        data = results_db_service.get_result_data("res-legacy")
    finally:
        mod_logger.removeHandler(capturer)

    assert data == b"legacy bytes"
    perf_msgs = [m for m in capturer.messages if "[PERF] get_result_data" in m]
    assert any("backend=db_legacy" in m for m in perf_msgs), perf_msgs
    assert not any("backend=db " in m for m in perf_msgs), perf_msgs


def test_get_result_data_logs_backend_object_storage_when_storage_key_set(
    monkeypatch,
):
    """Row con storage_key valorizzato: il log marker deve riportare
    `backend=object_storage` (non piu' `backend=s3` provider-hardcoded).
    Naming coerente con la nomenclatura architetturale (S3/MinIO vivono
    sotto la stessa astrazione runtime object storage).

    BE-GO-READY-RESOURCE-ENGINE-002: la dispatch S3 vive ora nel
    loader; il test patch `_download_object_storage` (canonical post-
    pilot symbol) invece del vecchio `_s3_download_bytes` (che e'
    ora un thin wrapper sul loader).
    """
    import app.db.pg_connection as pg_conn
    import app.core.resources.loaders.result_blob as result_blob_loader

    monkeypatch.setattr(
        pg_conn,
        "fetchone",
        lambda sql, params=(): {
            "data": None,
            "storage_key": "results/p1/r1.bin",
        },
    )
    monkeypatch.setattr(
        result_blob_loader,
        "_download_object_storage",
        lambda key: b"object storage bytes",
    )

    mod_logger, capturer = _attach_logger()
    try:
        data = results_db_service.get_result_data("res-obj")
    finally:
        mod_logger.removeHandler(capturer)

    assert data == b"object storage bytes"
    perf_msgs = [m for m in capturer.messages if "[PERF] get_result_data" in m]
    assert any("backend=object_storage" in m for m in perf_msgs), perf_msgs
    # Niente regression sui marker pre-rename:
    assert not any("backend=s3" in m for m in perf_msgs), perf_msgs
