"""
Smoke contract - STRUCT-001 Seam C: estrazione storage dispatch.

Verifica le invarianti minime del policy dispatch:
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
    """Patcher per `app.config.settings.get(...)` controllabile dal test.

    BE-CONFIG-001B: il dispatch ora legge anche
    `FILE_SOURCE_S3_THRESHOLD_BYTES` via Settings. Per altre chiavi
    (incluso il threshold) il fake delega al `Settings` reale, cosi' i
    test che usano `monkeypatch.setenv(...)` continuano a vedere
    l'override env tramite `_converti_tipo_env`. Solo `STORAGE_POLICY`
    e' forzato al valore del test.
    """
    from app.config import settings as _real_settings

    fake_settings = MagicMock()

    def _fake_get(key, default=None):
        if key == "STORAGE_POLICY":
            return policy_value
        return _real_settings.get(key, default)

    fake_settings.get = MagicMock(side_effect=_fake_get)
    return patch("app.config.settings", fake_settings)


# 1. Storage policy routing


@pytest.mark.parametrize(
    "policy, raw, threshold, expected_key",
    [
        ("db", b"x" * 2048, 100, None),
        ("object_storage", b"y" * 100, 1024, "file-sources/file_abc/f.csv"),
        ("hybrid", b"x" * 512, 1024, None),
        ("hybrid", b"y" * 2048, 1024, "file-sources/file_abc/f.csv"),
        ("hybrid", b"z" * 1024, 1024, "file-sources/file_abc/f.csv"),
        (None, b"x" * 512, 1024, None),
    ],
)
def test_dispatch_policy_routes_payload(
    fake_logger, fake_storage, monkeypatch, policy, raw, threshold, expected_key,
):
    from app.core.files.storage_dispatch import dispatch_blob_storage

    monkeypatch.setenv("FILE_SOURCE_S3_THRESHOLD_BYTES", str(threshold))
    with _patched_settings(policy), \
         patch("app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        out = dispatch_blob_storage("file_abc", raw, "f.csv", logger=fake_logger)

    assert out == {
        "data": None if expected_key else raw,
        "storage_key": expected_key,
        "size_bytes": len(raw),
    }
    if expected_key:
        fake_storage.upload_bytes.assert_called_once_with(raw, expected_key)
    else:
        fake_storage.upload_bytes.assert_not_called()


def test_dispatch_db_policy_does_not_log(fake_logger):
    """Path BYTEA: nessun log info/error (silenzioso)."""
    from app.core.files.storage_dispatch import dispatch_blob_storage

    with _patched_settings("db"):
        dispatch_blob_storage("file_x", b"data", "f.csv", logger=fake_logger)

    fake_logger.info.assert_not_called()
    fake_logger.error.assert_not_called()


# 2. Object storage upload failure


def test_dispatch_object_storage_policy_reraises_upload_exception(
    fake_logger, fake_storage,
):
    """Orphan guard: upload exception rilanciata, no row DB nel caller."""
    from app.core.files.storage_dispatch import dispatch_blob_storage

    fake_storage.upload_bytes.side_effect = RuntimeError("S3 down")
    with _patched_settings("object_storage"), \
         patch("app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        with pytest.raises(RuntimeError, match="S3 down"):
            dispatch_blob_storage("file_z", b"data", "f.csv", logger=fake_logger)

    fake_logger.error.assert_called_once()


# 3. Logger e' iniettato (non viene da FileSourceService)


def test_dispatch_uses_injected_logger_not_global(fake_storage):
    """Il logger e' parametro: due call con due logger distinti devono
    loggare ciascuno sul proprio."""
    from app.core.files.storage_dispatch import dispatch_blob_storage

    log_a = MagicMock()
    log_b = MagicMock()

    with _patched_settings("object_storage"), \
         patch("app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        dispatch_blob_storage("file_a", b"x", "a.csv", logger=log_a)
        dispatch_blob_storage("file_b", b"y", "b.csv", logger=log_b)

    log_a.info.assert_called_once()
    log_b.info.assert_called_once()
    log_a.info.call_args[0][0]
    # Cross check: il logger A non e' stato usato per il file_b e viceversa
    assert "file_a" in log_a.info.call_args[0][0]
    assert "file_b" in log_b.info.call_args[0][0]


# 4. Il facade FileSourceService._dispatch_blob_storage delega alla
#    funzione modulo.


def test_file_source_service_dispatch_facade_delegates_to_module_function(
    fake_logger, fake_storage,
):
    """`FileSourceService._dispatch_blob_storage` resta un facade sottile:
    riceve self._logger, lo passa alla funzione modulo, ritorna il dict."""
    from app.core.files.service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._logger = fake_logger

    raw = b"data" * 10
    with _patched_settings("db"), \
         patch("app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        out = svc._dispatch_blob_storage("file_w", raw, "w.csv")

    assert out["data"] == raw
    assert out["storage_key"] is None
    assert out["size_bytes"] == len(raw)


# 5. BE-HARDEN-001C E1: storage_key_id opzionale per shared blob multi-layer


def test_dispatch_uses_storage_key_id_when_provided(fake_logger, fake_storage):
    """Object storage path: se `storage_key_id` e' fornito, sostituisce
    `file_id` come segmento path. Permette al multi-layer upload di
    condividere 1 blob fisico tra N FileSource logici."""
    from app.core.files.storage_dispatch import dispatch_blob_storage

    raw = b"shared blob bytes" * 10
    with _patched_settings("object_storage"), \
         patch("app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        out = dispatch_blob_storage(
            "batch_abc", raw, "multi.gpkg",
            logger=fake_logger,
            storage_key_id="batch_abc",
        )

    expected_key = "file-sources/batch_abc/multi.gpkg"
    assert out == {"data": None, "storage_key": expected_key, "size_bytes": len(raw)}
    fake_storage.upload_bytes.assert_called_once_with(raw, expected_key)


def test_dispatch_storage_key_id_none_falls_back_to_file_id(
    fake_logger, fake_storage,
):
    """Single-file path: storage_key_id=None usa `file_id` nel path."""
    from app.core.files.storage_dispatch import dispatch_blob_storage

    raw = b"single file bytes"
    with _patched_settings("object_storage"), \
         patch("app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        out = dispatch_blob_storage(
            "file_single", raw, "single.csv",
            logger=fake_logger,
            storage_key_id=None,
        )

    expected_key = "file-sources/file_single/single.csv"
    assert out["storage_key"] == expected_key
    fake_storage.upload_bytes.assert_called_once_with(raw, expected_key)
