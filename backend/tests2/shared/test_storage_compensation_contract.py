"""
Contract test BE-GO-READY-STORAGE-ENGINE-002 — compensating-delete

helper unificato `app.shared.storage_compensation.compensating_delete`.
"""

from __future__ import annotations

import logging
from typing import List
from unittest.mock import MagicMock

import pytest

from app.shared.storage_compensation import compensating_delete


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# Stubs


class _StubStorage:
    """Minimal `ObjectStorage` stub. `delete` registra le chiavi
    cancellate; `delete_should_fail` simula errore S3."""

    def __init__(self, *, delete_should_fail: bool = False, exc: Exception | None = None):
        self.deletes: List[str] = []
        self._should_fail = delete_should_fail
        self._exc = exc or RuntimeError("S3 5xx")

    def delete(self, key: str) -> None:
        self.deletes.append(key)
        if self._should_fail:
            raise self._exc


@pytest.fixture
def caplog_helper(caplog):
    """Attach `caplog` al logger del modulo helper. Helper non setta
    `propagate=False` (e' uno stdlib `logging.getLogger`), ma
    rendiamo esplicito per stabilita'."""
    helper_logger = logging.getLogger("app.shared.storage_compensation")
    prev_level = helper_logger.level
    helper_logger.setLevel(logging.DEBUG)
    helper_logger.addHandler(caplog.handler)
    yield caplog
    helper_logger.removeHandler(caplog.handler)
    helper_logger.setLevel(prev_level)


# 1. Capability disabled -> skipped silenzioso


def test_capability_disabled_returns_skipped_no_factory_call(
    monkeypatch, caplog_helper,
):
    factory = MagicMock(side_effect=AssertionError("factory non deve essere chiamata"))
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.object_storage_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        factory,
    )

    out = compensating_delete(
        "results/p1/abc.bin",
        domain="result",
        reason="row_delete_post_commit",
    )

    assert out == "skipped"
    factory.assert_not_called()
    assert any(
        "event=storage_compensating_delete_skipped" in r.getMessage()
        and "skip_reason=capability_disabled" in r.getMessage()
        and "domain=result" in r.getMessage()
        for r in caplog_helper.records
    ), [r.getMessage() for r in caplog_helper.records]


def test_capability_disabled_with_supplied_storage_still_skipped(
    monkeypatch, caplog_helper,
):
    """Anche quando il caller passa un `storage` esplicito, la
    capability gate viene controllata prima per uniformita': il
    callsite non sa se la capability e' ancora attiva."""
    storage = _StubStorage()
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.object_storage_enabled",
        lambda: False,
    )

    out = compensating_delete(
        "file-sources/x/y.csv",
        domain="file_source",
        reason="row_delete_post_commit",
        storage=storage,
    )

    assert out == "skipped"
    assert storage.deletes == []


# 2. Capability ON + delete OK -> ok


def test_capability_on_delete_ok_returns_ok_emits_info_event(
    monkeypatch, caplog_helper,
):
    storage = _StubStorage()
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.object_storage_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        lambda: storage,
    )

    out = compensating_delete(
        "results/p1/abc.bin",
        domain="result",
        reason="db_save_failed_after_object_upload",
        extras={"project_id": "p1", "result_id": "r1"},
    )

    assert out == "ok"
    assert storage.deletes == ["results/p1/abc.bin"]
    msgs = [r.getMessage() for r in caplog_helper.records]
    assert any(
        "event=storage_compensating_delete_ok" in m
        and "domain=result" in m
        and "storage_key=results/p1/abc.bin" in m
        and "reason=db_save_failed_after_object_upload" in m
        and "project_id='p1'" in m
        and "result_id='r1'" in m
        for m in msgs
    ), msgs


def test_supplied_storage_used_instead_of_factory(monkeypatch):
    """Caller passa un'istanza -> nessun call a
    `get_runtime_object_storage()`."""
    storage = _StubStorage()
    factory = MagicMock(side_effect=AssertionError("factory non deve essere chiamata"))
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.object_storage_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        factory,
    )

    out = compensating_delete(
        "results/p1/abc.bin",
        domain="result",
        reason="db_save_failed_after_object_upload",
        storage=storage,
    )

    assert out == "ok"
    factory.assert_not_called()
    assert storage.deletes == ["results/p1/abc.bin"]


# 3. Capability ON + delete fail -> failed (mai solleva)


def test_delete_failure_returns_failed_never_raises(
    monkeypatch, caplog_helper,
):
    storage = _StubStorage(delete_should_fail=True, exc=RuntimeError("S3 503"))
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.object_storage_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        lambda: storage,
    )

    # Non solleva — questo e' il contract centrale del helper.
    out = compensating_delete(
        "file-sources/x/y.csv",
        domain="file_source",
        reason="row_delete_post_commit",
        extras={"file_id": "fs-1"},
    )

    assert out == "failed"
    msgs = [r.getMessage() for r in caplog_helper.records]
    assert any(
        "event=storage_compensating_delete_failed" in m
        and "domain=file_source" in m
        and "storage_key=file-sources/x/y.csv" in m
        and "reason=row_delete_post_commit" in m
        and "cleanup_error=RuntimeError" in m
        and "S3 503" in m
        and "file_id='fs-1'" in m
        for m in msgs
    ), msgs


def test_delete_failure_warning_log_level(monkeypatch, caplog_helper):
    """ADR-0011: orphan accepted on storage failure ⇒ log a livello
    `warning`, non `error`. Allerta attiva senza crashing semantics."""
    storage = _StubStorage(delete_should_fail=True)
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.object_storage_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        lambda: storage,
    )

    compensating_delete(
        "results/p1/x.bin",
        domain="result",
        reason="db_save_failed_after_object_upload",
    )

    failed = [
        r for r in caplog_helper.records
        if "event=storage_compensating_delete_failed" in r.getMessage()
    ]
    assert len(failed) == 1
    assert failed[0].levelno == logging.WARNING


# 4. Race capability-off-during-resolution


def test_race_capability_disabled_during_factory_resolution(
    monkeypatch, caplog_helper,
):
    """Capability era ON al gate, OFF quando la factory tenta di
    risolvere il client (race teorica). Outcome = `"skipped"` con
    `skip_reason=capability_disabled_race`."""
    from app.infrastructure.storage.cloud_backends import ObjectStorageDisabledError

    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.object_storage_enabled",
        lambda: True,
    )

    def _factory_disabled():
        raise ObjectStorageDisabledError("disabled mid-flight")

    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        _factory_disabled,
    )

    out = compensating_delete(
        "imdf-bundles/abc.zip",
        domain="imdf_bundle",
        reason="row_delete_post_commit",
    )

    assert out == "skipped"
    msgs = [r.getMessage() for r in caplog_helper.records]
    assert any(
        "event=storage_compensating_delete_skipped" in m
        and "skip_reason=capability_disabled_race" in m
        for m in msgs
    ), msgs


def test_factory_unexpected_error_returns_failed(monkeypatch, caplog_helper):
    """Factory solleva un errore non-disabled (es. boto config rotto):
    outcome `"failed"`, mai solleva."""
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.object_storage_enabled",
        lambda: True,
    )

    def _factory_explodes():
        raise RuntimeError("boto config invalid")

    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        _factory_explodes,
    )

    out = compensating_delete(
        "results/p/x.bin",
        domain="result",
        reason="db_save_failed_after_object_upload",
    )

    assert out == "failed"
    msgs = [r.getMessage() for r in caplog_helper.records]
    assert any(
        "event=storage_compensating_delete_failed" in m
        and "cleanup_error=RuntimeError" in m
        for m in msgs
    ), msgs


# 5. Extras handling: protected keys non sovrascrivono i campi canonici


def test_extras_protected_keys_filtered(monkeypatch, caplog_helper):
    """Se il caller passa `extras` con chiavi protette (es. `event`,
    `domain`, `storage_key`), queste NON devono apparire nel log
    suffix per non collidere con i campi canonici."""
    storage = _StubStorage()
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.object_storage_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        lambda: storage,
    )

    compensating_delete(
        "results/p/x.bin",
        domain="result",
        reason="row_delete_post_commit",
        extras={
            "event": "MALICIOUS",
            "domain": "MALICIOUS",
            "storage_key": "MALICIOUS",
            "reason": "MALICIOUS",
            "outcome": "MALICIOUS",
            "file_id": "fs-1",  # safe key
        },
    )

    msgs = [r.getMessage() for r in caplog_helper.records]
    ok = next(m for m in msgs if "event=storage_compensating_delete_ok" in m)
    # Canonici intatti
    assert "domain=result" in ok
    assert "storage_key=results/p/x.bin" in ok
    assert "reason=row_delete_post_commit" in ok
    # Le chiavi protette NON appaiono come `key=MALICIOUS`
    assert "MALICIOUS" not in ok
    # La chiave safe e' presente
    assert "file_id='fs-1'" in ok


def test_extras_none_value_omitted(monkeypatch, caplog_helper):
    """Valori `None` negli extras vengono omessi per non sporcare il log."""
    storage = _StubStorage()
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.object_storage_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        lambda: storage,
    )

    compensating_delete(
        "results/p/x.bin",
        domain="result",
        reason="row_delete_post_commit",
        extras={"file_id": "fs-1", "batch_id": None},
    )

    msgs = [r.getMessage() for r in caplog_helper.records]
    ok = next(m for m in msgs if "event=storage_compensating_delete_ok" in m)
    assert "file_id='fs-1'" in ok
    assert "batch_id" not in ok


# 6. Caller-supplied logger


def test_caller_logger_receives_records(monkeypatch):
    """Quando il caller passa `logger=...`, il record va a quel logger
    (e non al helper-default)."""
    storage = _StubStorage()
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.object_storage_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        lambda: storage,
    )

    fake_logger = MagicMock()
    out = compensating_delete(
        "results/p/x.bin",
        domain="result",
        reason="row_delete_post_commit",
        logger=fake_logger,
    )

    assert out == "ok"
    fake_logger.info.assert_called_once()
    template = fake_logger.info.call_args[0][0]
    assert "event=storage_compensating_delete_ok" in template
