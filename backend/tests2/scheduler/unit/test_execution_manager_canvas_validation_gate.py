"""Contract — gate canvas-validation per i run schedulati (Epic CanvasVal §12.5).

Dietro lo stesso feature-flag del gate a esecuzione (CANVAS_VALIDATION_GATE_ENABLED,
default off): se il progetto non è validato (``canvas_validated`` persistito), il run
schedulato NON parte — registra un evento PIPELINE_FAILED (canale notifiche dello
scheduler) e ritorna failed, PRIMA di acquisire il lock / aprire il run.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.scheduler.execution_manager import ExecutionManager


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def _make_manager() -> ExecutionManager:
    manager = ExecutionManager.__new__(ExecutionManager)
    manager.schedule_service = MagicMock()
    manager.log_service = MagicMock()
    manager.concurrency = MagicMock()
    manager.policy = MagicMock()
    manager._connection_service = None
    manager._artifact_store = MagicMock()
    return manager


def _meta(canvas_validated):
    return SimpleNamespace(
        id="proj-1", group_id="grp-A", project_type="estrai",
        canvas_validated=canvas_validated,
    )


def _flag(value):
    return patch(
        "app.config.settings.get",
        side_effect=lambda k, d=None: value if k == "CANVAS_VALIDATION_GATE_ENABLED" else d,
    )


def _run(manager, *, meta, acquire="skipped"):
    manager.concurrency.try_acquire = MagicMock(return_value=acquire)
    with patch(
        "app.core.scheduler.db.get_schedule_row",
        return_value=SimpleNamespace(id="s-1", group_id="grp-A"),
    ), patch("app.core.projects.service.ProjectService") as psc:
        psc.return_value.get_project_metadata.return_value = meta
        return manager.execute("s-1")


def test_blocks_scheduled_run_when_not_validated_and_flag_on(schedule_config):
    manager = _make_manager()
    manager.schedule_service.get_schedule = MagicMock(return_value=schedule_config)
    with _flag(True):
        result = _run(manager, meta=_meta(False))

    assert result["success"] is False
    assert result["status"] == "failed"
    assert "validato" in result["message"].lower()
    # NON deve acquisire il lock né aprire il run (short-circuit prima)
    manager.concurrency.try_acquire.assert_not_called()
    # evento di notifica scritto
    kwargs = manager.log_service.create_log_entry.call_args.kwargs
    assert kwargs.get("event") == "PIPELINE_FAILED"
    assert "validato" in kwargs.get("error_message", "").lower()


def test_does_not_block_when_validated(schedule_config):
    manager = _make_manager()
    manager.schedule_service.get_schedule = MagicMock(return_value=schedule_config)
    with _flag(True):
        result = _run(manager, meta=_meta(True))
    # gate superato → arriva a try_acquire (qui "skipped" → ritorno pulito)
    manager.concurrency.try_acquire.assert_called_once()
    assert result["status"] == "skipped"


def test_does_not_block_when_flag_off_even_if_not_validated(schedule_config):
    manager = _make_manager()
    manager.schedule_service.get_schedule = MagicMock(return_value=schedule_config)
    with _flag(False):
        result = _run(manager, meta=_meta(False))
    # flag off → gate saltato → arriva a try_acquire
    manager.concurrency.try_acquire.assert_called_once()
    assert result["status"] == "skipped"
