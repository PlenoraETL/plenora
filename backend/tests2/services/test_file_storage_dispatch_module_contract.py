"""Smoke contract - STRUCT-001 Seam C: estrazione storage dispatch.

Verifica le invarianti minime del policy dispatch:

  1. STORAGE_POLICY=db   -> sempre BYTEA (data=raw_data, storage_key=None).
  2. STORAGE_POLICY=object_storage -> sempre object storage upload
     (data=None, storage_key=`file-sources/<id>/<filename>`).
  3. STORAGE_POLICY=hybrid (default) -> threshold-based:
     - size < threshold -> BYTEA
     - size >= threshold -> object storage
     Threshold default 50 MB, override via FILE_SOURCE_S3_THRESHOLD_BYTES.
  4. Logger e' iniettato come parametro (non importa FileSourceService).
  5. Upload exception viene rilanciata (orphan guard nel caller).

Mock minimale di `app.shared.cloud_backends.get_runtime_object_storage`:
nessun S3 reale, nessuna dipendenza di rete.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.fixture
def fake_storage():
    """Mock object storage client con upload_bytes() registrato."""
    storage = MagicMock()
    storage.upload_bytes = MagicMock(return_value=None)
    return storage


@pytest.fixture
def fake_logger():
    """Logger mock per LoggerProtocol-like (info/error/warning/debug)."""
    log = MagicMock()
    log.info = MagicMock()
    log.error = MagicMock()
    log.warning = MagicMock()
    log.debug = MagicMock()
    return log


def _patched_settings(policy_value):
    """Patcher per `app.config.settings.get('STORAGE_POLICY', ...)`."""
    fake_settings = MagicMock()
    fake_settings.get = MagicMock(side_effect=lambda key, default=None: (
        policy_value if key == "STORAGE_POLICY" else default
    ))
    return patch("app.config.settings", fake_settings)


# =============================================================================
# 1. STORAGE_POLICY=db -> sempre DB BYTEA
# =============================================================================


def test_dispatch_db_policy_always_returns_bytea(fake_logger, fake_storage):
    from app.services.file_storage_dispatch import dispatch_blob_storage

    raw = b"x" * 2048
    with _patched_settings("db"), \
         patch("app.shared.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        out = dispatch_blob_storage("file_abc", raw, "f.csv", logger=fake_logger)

    assert out == {"data": raw, "storage_key": None, "size_bytes": 2048}
    fake_storage.upload_bytes.assert_not_called()


def test_dispatch_db_policy_does_not_log(fake_logger):
    """Path BYTEA: nessun log info/error (silenzioso)."""
    from app.services.file_storage_dispatch import dispatch_blob_storage

    with _patched_settings("db"):
        dispatch_blob_storage("file_x", b"data", "f.csv", logger=fake_logger)

    fake_logger.info.assert_not_called()
    fake_logger.error.assert_not_called()


# =============================================================================
# 2. STORAGE_POLICY=object_storage -> sempre object storage
# =============================================================================


def test_dispatch_object_storage_policy_uploads_and_returns_storage_key(
    fake_logger, fake_storage,
):
    from app.services.file_storage_dispatch import dispatch_blob_storage

    raw = b"y" * 100
    with _patched_settings("object_storage"), \
         patch("app.shared.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        out = dispatch_blob_storage("file_xyz", raw, "tiny.csv", logger=fake_logger)

    expected_key = "file-sources/file_xyz/tiny.csv"
    assert out == {"data": None, "storage_key": expected_key, "size_bytes": 100}
    fake_storage.upload_bytes.assert_called_once_with(raw, expected_key)
    fake_logger.info.assert_called_once()


def test_dispatch_object_storage_policy_reraises_upload_exception(
    fake_logger, fake_storage,
):
    """Orphan guard: upload exception rilanciata, no row DB nel caller."""
    from app.services.file_storage_dispatch import dispatch_blob_storage

    fake_storage.upload_bytes.side_effect = RuntimeError("S3 down")
    with _patched_settings("object_storage"), \
         patch("app.shared.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        with pytest.raises(RuntimeError, match="S3 down"):
            dispatch_blob_storage("file_z", b"data", "f.csv", logger=fake_logger)

    fake_logger.error.assert_called_once()


# =============================================================================
# 3. STORAGE_POLICY=hybrid -> threshold-based
# =============================================================================


def test_dispatch_hybrid_policy_below_threshold_uses_bytea(
    fake_logger, fake_storage, monkeypatch,
):
    from app.services.file_storage_dispatch import dispatch_blob_storage

    monkeypatch.setenv("FILE_SOURCE_S3_THRESHOLD_BYTES", str(1024))
    raw = b"x" * 512  # sotto soglia (1024)
    with _patched_settings("hybrid"), \
         patch("app.shared.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        out = dispatch_blob_storage("file_a", raw, "f.csv", logger=fake_logger)

    assert out["data"] == raw
    assert out["storage_key"] is None
    assert out["size_bytes"] == 512
    fake_storage.upload_bytes.assert_not_called()


def test_dispatch_hybrid_policy_at_or_above_threshold_uses_object_storage(
    fake_logger, fake_storage, monkeypatch,
):
    from app.services.file_storage_dispatch import dispatch_blob_storage

    monkeypatch.setenv("FILE_SOURCE_S3_THRESHOLD_BYTES", str(1024))
    raw = b"y" * 2048  # sopra soglia (1024)
    with _patched_settings("hybrid"), \
         patch("app.shared.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        out = dispatch_blob_storage("file_b", raw, "big.csv", logger=fake_logger)

    assert out["data"] is None
    assert out["storage_key"] == "file-sources/file_b/big.csv"
    assert out["size_bytes"] == 2048
    fake_storage.upload_bytes.assert_called_once()


def test_dispatch_hybrid_policy_at_exact_threshold_uses_object_storage(
    fake_logger, fake_storage, monkeypatch,
):
    """Boundary: size == threshold -> object storage (>= confronto)."""
    from app.services.file_storage_dispatch import dispatch_blob_storage

    monkeypatch.setenv("FILE_SOURCE_S3_THRESHOLD_BYTES", str(1024))
    raw = b"z" * 1024  # esattamente la soglia
    with _patched_settings("hybrid"), \
         patch("app.shared.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        out = dispatch_blob_storage("file_c", raw, "boundary.csv", logger=fake_logger)

    assert out["data"] is None
    assert out["storage_key"] == "file-sources/file_c/boundary.csv"


def test_dispatch_falls_back_to_hybrid_when_settings_returns_none(
    fake_logger, fake_storage, monkeypatch,
):
    """STORAGE_POLICY presente come None -> fallback `or 'hybrid'`.

    Esercita il path `(settings.get(...) or 'hybrid')`: la fixture forza
    settings.get('STORAGE_POLICY', ...) a ritornare None (ignorando il
    default), cosi' il codice deve cadere sul `or 'hybrid'` e poi
    valutare la threshold.
    """
    from app.services.file_storage_dispatch import dispatch_blob_storage

    monkeypatch.setenv("FILE_SOURCE_S3_THRESHOLD_BYTES", str(1024))
    raw = b"x" * 512  # sotto soglia: se il fallback 'hybrid' funziona -> BYTEA
    with _patched_settings(None), \
         patch("app.shared.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        out = dispatch_blob_storage("file_d", raw, "f.csv", logger=fake_logger)

    assert out["data"] == raw
    assert out["storage_key"] is None
    fake_storage.upload_bytes.assert_not_called()


def test_dispatch_explicit_hybrid_policy_under_threshold_uses_bytea(
    fake_logger, fake_storage, monkeypatch,
):
    """STORAGE_POLICY='hybrid' esplicito (no fallback) - smoke gemello del
    test sopra per separare le due semantiche (None vs 'hybrid' explicit)."""
    from app.services.file_storage_dispatch import dispatch_blob_storage

    monkeypatch.setenv("FILE_SOURCE_S3_THRESHOLD_BYTES", str(1024))
    raw = b"x" * 512
    with _patched_settings("hybrid"), \
         patch("app.shared.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        out = dispatch_blob_storage("file_e", raw, "f.csv", logger=fake_logger)

    assert out["data"] == raw
    assert out["storage_key"] is None


# =============================================================================
# 4. Logger e' iniettato (non viene da FileSourceService)
# =============================================================================


def test_dispatch_uses_injected_logger_not_global(fake_storage):
    """Il logger e' parametro: due call con due logger distinti devono
    loggare ciascuno sul proprio."""
    from app.services.file_storage_dispatch import dispatch_blob_storage

    log_a = MagicMock()
    log_b = MagicMock()

    with _patched_settings("object_storage"), \
         patch("app.shared.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        dispatch_blob_storage("file_a", b"x", "a.csv", logger=log_a)
        dispatch_blob_storage("file_b", b"y", "b.csv", logger=log_b)

    log_a.info.assert_called_once()
    log_b.info.assert_called_once()
    log_a.info.call_args[0][0]
    # Cross check: il logger A non e' stato usato per il file_b e viceversa
    assert "file_a" in log_a.info.call_args[0][0]
    assert "file_b" in log_b.info.call_args[0][0]


# =============================================================================
# 5. Backward-compat: il wrapper FileSourceService._dispatch_blob_storage
#    delega correttamente alla funzione modulo.
# =============================================================================


def test_file_service_wrapper_delegates_to_module_function(fake_logger, fake_storage):
    """`FileSourceService._dispatch_blob_storage` resta come thin wrapper:
    riceve self._logger, lo passa alla funzione modulo, ritorna il dict."""
    from app.services.file_service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._logger = fake_logger

    raw = b"data" * 10
    with _patched_settings("db"), \
         patch("app.shared.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        out = svc._dispatch_blob_storage("file_w", raw, "w.csv")

    assert out["data"] == raw
    assert out["storage_key"] is None
    assert out["size_bytes"] == len(raw)
