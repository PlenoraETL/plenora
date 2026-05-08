"""
Persistenza behavior-bound di scheduled_for nei log/metadata.

Contract: il kwarg `scheduled_for` (passato dal beat al task in caso di
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.scheduler.execution_manager import ExecutionManager


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


_SCHEDULED_FOR = "2026-04-17T10:00:00+00:00"


def _project_meta(group_id: str):
    return SimpleNamespace(id="proj-1", group_id=group_id, project_type="estrai")


def _make_orchestrator_result(rows: int = 10, output: str = "/tmp/run.csv"):
    """Risultato orchestrator success path. `output_path` valorizzato
    cosi' il branch di materializzazione blob (`if not output_file`)
    in `execute()` viene saltato — non ci serve qui."""
    res = MagicMock()
    res.success = True
    res.rows_count = rows
    res.output_path = output
    res.message = "ok"
    res.to_run_artifact_ref = MagicMock(return_value=None)
    return res


def _make_manager_for_success_path(schedule_config) -> ExecutionManager:
    """ExecutionManager con tutte le dipendenze stubbate per il success
    path, fino a `log_service.create_log_entry`.

    Pattern derivato dai due `_make_manager` esistenti nel modulo
    (`test_execution_manager_contract.py`,
    `test_execution_manager_orphan_and_release_contract.py`) +
    short-circuit su pre_check / orchestrator / carica / artifact_store
    / policy / heartbeat. Niente DB, Redis, FS o thread reali.
    """
    manager = ExecutionManager.__new__(ExecutionManager)

    manager.schedule_service = MagicMock()
    manager.schedule_service.get_schedule = MagicMock(return_value=schedule_config)

    manager.log_service = MagicMock()
    manager.log_service.write_log = MagicMock(return_value="/tmp/log.json")

    manager.concurrency = MagicMock()
    manager.concurrency.try_acquire = MagicMock(return_value="acquired")
    manager.concurrency.release = MagicMock()

    manager.policy = MagicMock()
    manager.policy.decide_after_success = MagicMock(
        return_value={
            "status": "completed",
            "event": "PIPELINE_COMPLETED",
            "log_level": "INFO",
        }
    )

    manager._connection_service = None
    manager._get_connection_service = MagicMock(return_value=MagicMock())

    manager._artifact_store = MagicMock()
    manager._artifact_store.archive = MagicMock(return_value="/tmp/archived.csv")

    manager._start_lock_heartbeat = MagicMock(
        return_value={"stop_event": None, "thread": None, "lock_lost": False}
    )
    manager._stop_lock_heartbeat = MagicMock()
    manager._pre_check_connection = MagicMock(return_value=True)
    manager._execute_carica = MagicMock(
        return_value={
            "success": True,
            "rows_written": 10,
            "rows_skipped": 0,
            "rows_inserted": 10,
            "rows_updated": 0,
        }
    )

    fake_orchestrator = MagicMock()
    fake_orchestrator.execute_pipeline = MagicMock(
        return_value=_make_orchestrator_result()
    )
    manager._get_orchestrator = MagicMock(return_value=fake_orchestrator)

    return manager


def _patches():
    """Patch context comuni per arrivare al success path di execute()."""
    return (
        patch(
            "app.core.scheduler.db.get_schedule",
            return_value={"id": "sch-1", "group_id": "grp-A"},
        ),
        patch("app.services.project_service.ProjectService"),
        patch("app.core.scheduler.execution_manager.open_run"),
        patch("app.core.scheduler.execution_manager.set_run_context"),
        patch("app.core.scheduler.execution_manager.close_run"),
    )


def test_execute_persists_scheduled_for_in_schedule_info_and_execution_info(
    schedule_config,
):
    """`scheduled_for` deve raggiungere il log entry come campo
    `schedule_info.scheduled_for` E `execution_info.scheduled_for`.

    Coverage equivalente al sentinel source-bound pre-TR-002:
    "scheduled_for compare nel body di execute() vicino a
    create_log_entry / schedule_info / execution_info".
    """
    manager = _make_manager_for_success_path(schedule_config)
    p_db, p_proj, p_open, p_ctx, p_close = _patches()

    with p_db, p_proj as project_service_cls, p_open, p_ctx, p_close:
        project_service_cls.return_value.get_project_metadata.return_value = (
            _project_meta("grp-A")
        )
        result = manager.execute(
            "sch-1",
            triggered_by="catchup",
            scheduled_for=_SCHEDULED_FOR,
        )

    assert result["success"] is True, f"Expected success, got {result!r}"

    manager.log_service.create_log_entry.assert_called_once()
    kw = manager.log_service.create_log_entry.call_args.kwargs

    schedule_info = kw.get("schedule_info") or {}
    execution_info = kw.get("execution_info") or {}

    assert schedule_info.get("scheduled_for") == _SCHEDULED_FOR, (
        "scheduled_for non e' nel schedule_info del log entry — il "
        "lineage del catchup tick e' perso. "
        f"schedule_info={schedule_info!r}"
    )
    assert execution_info.get("scheduled_for") == _SCHEDULED_FOR, (
        "scheduled_for non e' nel execution_info del log entry — il "
        "lineage del catchup tick e' perso. "
        f"execution_info={execution_info!r}"
    )


def test_execute_propagates_scheduled_for_to_carica_kwarg(schedule_config):
    """`scheduled_for` deve essere propagato a `_execute_carica` come
    kwarg, cosi' la chiave idempotency CARICA (N15:
    `schedule-{id}-{scheduled_for}-carica`) e' deterministica sul tick.

    Senza questo, il retry dello stesso tick non riusa la stessa chiave
    e il guard idempotency e' no-op → possibile duplicazione su
    `write_mode=append`.
    """
    manager = _make_manager_for_success_path(schedule_config)
    p_db, p_proj, p_open, p_ctx, p_close = _patches()

    with p_db, p_proj as project_service_cls, p_open, p_ctx, p_close:
        project_service_cls.return_value.get_project_metadata.return_value = (
            _project_meta("grp-A")
        )
        manager.execute(
            "sch-1",
            triggered_by="catchup",
            scheduled_for=_SCHEDULED_FOR,
        )

    manager._execute_carica.assert_called_once()
    call = manager._execute_carica.call_args
    # `execute()` chiama `_execute_carica(config, result, scheduled_for=...)`
    # — kwarg esplicito.
    assert call.kwargs.get("scheduled_for") == _SCHEDULED_FOR, (
        "scheduled_for non e' propagato al _execute_carica kwarg → "
        "la chiave idempotency CARICA (N15) non e' stabile sul tick. "
        f"call.kwargs={call.kwargs!r}"
    )


def test_execute_with_scheduled_for_none_persists_none_in_log_entry(
    schedule_config,
):
    """Sanity: se `scheduled_for` non e' valorizzato (cron/manual run
    pre-N15b dove il task non aveva ancora il fallback al wallclock),
    il log entry persiste comunque la chiave (con valore None) e non
    crasha. Garantisce che l'operatore vede esplicitamente "no tick
    logico" invece di un campo mancante."""
    manager = _make_manager_for_success_path(schedule_config)
    p_db, p_proj, p_open, p_ctx, p_close = _patches()

    with p_db, p_proj as project_service_cls, p_open, p_ctx, p_close:
        project_service_cls.return_value.get_project_metadata.return_value = (
            _project_meta("grp-A")
        )
        result = manager.execute("sch-1", triggered_by="cron")

    assert result["success"] is True
    kw = manager.log_service.create_log_entry.call_args.kwargs
    schedule_info = kw.get("schedule_info") or {}
    execution_info = kw.get("execution_info") or {}
    assert "scheduled_for" in schedule_info
    assert schedule_info["scheduled_for"] is None
    assert "scheduled_for" in execution_info
    assert execution_info["scheduled_for"] is None
