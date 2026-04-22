"""Contract test per breadcrumb ``logger.debug`` su silent skip (Batch 8 C2).

Blinda il fix §6.2 F6.1-F6.5 (audit storage/provider 2026-04-22, smell S9+S10):
ogni path che prima era ``except X: pass``/``continue`` senza visibilità ora
emette un ``logger.debug`` con il ``type(e).__name__`` e l'eccezione, così
che in prod sia possibile riconoscere optional-feature skip vs bug latente.

Pattern testato (rappresentativo, uno per file del batch):
- ftp._maybe_hash: hash opzionale fallisce → return None con breadcrumb
- sftp.disconnect: close() fallisce → swallow con breadcrumb
- local list_files: OSError su stat → continue con breadcrumb
- service.mark_as_processed: file_info fallisce → swallow con breadcrumb
- resolver._write_temp_blob: TEMP_DIR config fallisce → swallow con breadcrumb

Invariants bloccati:
- il flow resta invariato (return/continue/raise esterno preservati)
- il breadcrumb contiene ``type(e).__name__`` (per distinguere classi di errore)
- il livello è DEBUG (non inquina prod log che filtra >=INFO/WARNING)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.shared]


class _RecordCollector(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _attach_collector(logger_name: str) -> tuple[_RecordCollector, logging.Logger]:
    logger = logging.getLogger(logger_name)
    handler = _RecordCollector()
    # Forza il livello almeno a DEBUG: altrimenti i record filtrati a livello
    # logger non raggiungono il nostro handler.
    prev_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    return handler, logger, prev_level  # type: ignore[return-value]


def _detach_collector(logger: logging.Logger, handler: _RecordCollector, prev_level: int) -> None:
    logger.removeHandler(handler)
    logger.setLevel(prev_level)


# ─── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _ensure_database_url_for_module_import(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://fake:fake@localhost:5432/fake",
    )
    yield


# ─── 1) ftp._maybe_hash ────────────────────────────────────────────────────

def test_ftp_maybe_hash_logs_debug_on_silent_return_none(monkeypatch):
    """``_maybe_hash`` ritorna None quando hash fallisce. Ora con breadcrumb."""
    from app.core.datasources.ftp import FTPDataSource

    handler, logger, prev = _attach_collector("app.core.datasources.ftp")
    try:
        ds = FTPDataSource.__new__(FTPDataSource)

        def _raising_read(path):
            raise RuntimeError("hash boom token=LEAK")

        monkeypatch.setattr(ds, "read_file", _raising_read, raising=False)

        result = ds._maybe_hash("/some/path.csv", include_hash=True)

        # Flow invariato: return None su hash fallito
        assert result is None

        # Breadcrumb: un record DEBUG con contesto
        debug_records = [r for r in handler.records if r.levelno == logging.DEBUG]
        assert debug_records, "Atteso almeno un logger.debug su hash fail"
        msg = debug_records[-1].getMessage()
        assert "RuntimeError" in msg, (
            f"Il breadcrumb DEVE includere type(e).__name__ per classificare, "
            f"ottenuto: {msg!r}"
        )
        assert "/some/path.csv" in msg, (
            f"Il breadcrumb DEVE includere il path per correlazione, "
            f"ottenuto: {msg!r}"
        )
    finally:
        _detach_collector(logger, handler, prev)


# ─── 2) sftp.disconnect ────────────────────────────────────────────────────

def test_sftp_disconnect_logs_debug_on_close_failure():
    """``disconnect`` chiama close() su ssh/sftp: se close fallisce → breadcrumb."""
    from app.core.datasources.sftp import SFTPDataSource

    handler, logger, prev = _attach_collector("app.core.datasources.sftp")
    try:
        ds = SFTPDataSource.__new__(SFTPDataSource)

        class _ExplodingClose:
            def close(self):
                raise OSError("already closed")

        ds._sftp = _ExplodingClose()
        ds._ssh = None

        # Flow invariato: disconnect NON solleva — silent
        ds.disconnect()
        assert ds._sftp is None
        assert ds._ssh is None

        debug_records = [r for r in handler.records if r.levelno == logging.DEBUG]
        assert debug_records, "Atteso almeno un logger.debug su close fail"
        msg = debug_records[-1].getMessage()
        assert "OSError" in msg, (
            f"Il breadcrumb DEVE includere type(e).__name__, ottenuto: {msg!r}"
        )
    finally:
        _detach_collector(logger, handler, prev)


# ─── 3) local list_files: stat fail → continue con breadcrumb ─────────────

def test_local_list_files_logs_debug_on_stat_failure(tmp_path, monkeypatch):
    """``list_files`` con entry che solleva OSError in stat: skip + breadcrumb."""
    from app.core.datasources.local import LocalDataSource
    from app.core.datasources.interface import BackendConfig

    # Setup: un file reale + monkeypatch os.stat per far fallire SOLO su quello
    good_file = tmp_path / "good.txt"
    good_file.write_text("content")
    bad_file = tmp_path / "bad.txt"
    bad_file.write_text("content")

    original_stat = os.stat
    original_entry_stat = os.DirEntry.stat if hasattr(os, "DirEntry") else None

    def _selective_stat(path, *args, **kwargs):
        # Lavora sia con path string sia con Path
        if str(path).endswith("bad.txt"):
            raise OSError("ACL simulated deny")
        return original_stat(path, *args, **kwargs)

    config = BackendConfig(
        id="ds-test",
        type="local",
        root_path=str(tmp_path),
    )
    ds = LocalDataSource(config)

    handler, logger, prev = _attach_collector("app.core.datasources.local")
    try:
        # Usiamo os.scandir del ramo non-ricorsivo: patchiamo il metodo stat
        # delle DirEntry. Approccio piu' pulito: monkeypatch os.stat se richiamato
        # indirettamente. Poiche' list_files non-ricorsivo usa entry.stat(), va
        # patchato a livello entry. Semplifichiamo usando il ramo ricorsivo
        # che usa os.stat diretto.
        monkeypatch.setattr(os, "stat", _selective_stat)

        results = ds.list_files(path="", pattern=None, include_hash=False, recursive=True)

        # Flow invariato: il file buono è presente, quello cattivo è skippato
        names = {r.name for r in results}
        assert "good.txt" in names, (
            f"Il file stat-ok DEVE essere listato: ottenuto {names}"
        )
        assert "bad.txt" not in names, (
            f"Il file con stat-fail DEVE essere skippato: ottenuto {names}"
        )

        debug_records = [r for r in handler.records if r.levelno == logging.DEBUG]
        assert debug_records, "Atteso almeno un logger.debug su stat fail"
        msg = " ".join(r.getMessage() for r in debug_records)
        assert "OSError" in msg, (
            f"Il breadcrumb DEVE includere type(e).__name__, ottenuto: {msg!r}"
        )
        assert "bad.txt" in msg, (
            f"Il breadcrumb DEVE includere il path dell'entry skippata, "
            f"ottenuto: {msg!r}"
        )
    finally:
        _detach_collector(logger, handler, prev)


# ─── 4) service.mark_as_processed: snapshot fail → breadcrumb ─────────────

def test_service_mark_as_processed_logs_debug_on_snapshot_failure(
    tmp_path, monkeypatch,
):
    """``mark_as_processed`` appende snapshot best-effort: se file_info fallisce
    → swallow + breadcrumb (non rompe il flow di processed append + save).
    """
    from app.core.datasources.service import DataSourceService

    service = DataSourceService()
    service._scan_dir = tmp_path

    # _get_full_config chiamato da file_info → backend load. Facciamo direttamente
    # esplodere file_info via monkeypatch.
    def _raising_file_info(ds_id, filename):
        raise RuntimeError("backend unreachable")

    monkeypatch.setattr(service, "file_info", _raising_file_info)

    # Collector sul logger del service (StructuredLogger → underlying Python logger).
    handler, logger, prev = _attach_collector("app.core.datasources.service")
    try:
        # Non deve sollevare
        service.mark_as_processed("ds-x", "file.csv", "pipeline-1")

        debug_records = [r for r in handler.records if r.levelno == logging.DEBUG]
        assert debug_records, "Atteso almeno un logger.debug su file_info fail"
        msg = " ".join(r.getMessage() for r in debug_records)
        assert "RuntimeError" in msg, (
            f"Il breadcrumb DEVE includere type(e).__name__, ottenuto: {msg!r}"
        )
        assert "ds-x" in msg and "file.csv" in msg, (
            f"Il breadcrumb DEVE includere ds_id + filename per correlazione, "
            f"ottenuto: {msg!r}"
        )
    finally:
        _detach_collector(logger, handler, prev)


# ─── 5) resolver._write_temp_blob: TEMP_DIR config skip → breadcrumb ──────

def test_resolver_write_temp_blob_logs_debug_on_temp_dir_config_failure(
    monkeypatch,
):
    """La lettura di ``settings.TEMP_DIR`` è best-effort: se l'import di
    app.config fallisce, tempfile va su default di sistema e breadcrumb DEBUG.

    Simuliamo il fail iniettando un settings che solleva AttributeError
    nell'accesso a ``TEMP_DIR`` tramite ``getattr`` — meno invasivo che
    rompere l'import module-level.
    """
    from app.core.artifacts.resolver import ArtifactResolver

    resolver = ArtifactResolver.__new__(ArtifactResolver)

    # getattr con default non solleva; per far fallire forziamo una property
    # che solleva. Patchamo l'import `from app.config import settings as _settings`
    # forzando il modulo app.config a sollevare nel re-import interno.
    import app.config as app_config
    original_settings = app_config.settings

    class _ExplodingSettings:
        def __getattribute__(self, name):
            if name == "TEMP_DIR":
                raise RuntimeError("config read boom")
            return getattr(original_settings, name)

    monkeypatch.setattr(app_config, "settings", _ExplodingSettings())

    handler, logger, prev = _attach_collector("app.core.artifacts.resolver")
    try:
        # _write_temp_blob scrive un blob e ritorna path — flow invariato
        blob = b"hello breadcrumb"
        path = resolver._write_temp_blob(blob, fmt="txt")
        try:
            assert path.exists()
            assert path.read_bytes() == blob

            debug_records = [r for r in handler.records if r.levelno == logging.DEBUG]
            assert debug_records, (
                "Atteso almeno un logger.debug su TEMP_DIR config fail"
            )
            msg = " ".join(r.getMessage() for r in debug_records)
            assert "RuntimeError" in msg, (
                f"Il breadcrumb DEVE includere type(e).__name__, ottenuto: {msg!r}"
            )
            assert "TEMP_DIR" in msg, (
                f"Il breadcrumb DEVE identificare il contesto (TEMP_DIR), "
                f"ottenuto: {msg!r}"
            )
        finally:
            # Cleanup del tempfile creato
            try:
                path.unlink()
            except OSError:
                pass
    finally:
        _detach_collector(logger, handler, prev)
