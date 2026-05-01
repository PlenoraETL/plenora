"""Contract del kwarg `scheduled_for` su schedule_pipeline_task.

Il beat, quando emette un catchup run, passa un `scheduled_for` ISO al
task. Il task deve:
1. Accettare il kwarg senza errori (retrocompat: default None).
2. Propagarlo all'`ExecutionManager.execute` come kwarg esplicito,
   cosi' il valore raggiunge i log entry e la chiave idempotency CARICA.

TR-002 (residuo ADR-0020 Cat-3): pre-modernizzazione il check (2)
era source-bound (`source.count("scheduled_for") >= 2`). Post-TR-002 e'
behavior-bound: si invoca il task con scheduled_for esplicito e si
verifica che arrivi a `ExecutionManager.execute(scheduled_for=...)`.
La copertura "compute-when-None" resta in
`test_n15b_scheduled_for_always_set_contract.py` (gia' behavior-bound).
"""

import inspect

import pytest
from unittest.mock import patch

from workers.tasks.schedule_task import schedule_pipeline_task


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def test_schedule_pipeline_task_accepts_scheduled_for_kwarg():
    """Il task deve dichiarare `scheduled_for` tra i parametri (default None)."""
    sig = inspect.signature(schedule_pipeline_task)
    params = sig.parameters
    assert "scheduled_for" in params
    # Default deve essere None per retrocompat con chiamate non-catchup
    assert params["scheduled_for"].default is None


def test_task_propagates_scheduled_for_to_execution_manager():
    """Il task deve propagare `scheduled_for` a `ExecutionManager.execute`,
    non solo dichiararlo. Equivalente behavior-bound del sentinel
    source-bound pre-TR-002 ("kwarg dichiarato ma non usato")."""
    from workers.tasks import schedule_task

    captured: dict = {}

    class _MockManager:
        def execute(self, **kwargs):
            captured.update(kwargs)
            return {"success": True, "status": "completed"}

    with patch(
        "app.core.scheduler.execution_manager.ExecutionManager",
        return_value=_MockManager(),
    ):
        schedule_task.schedule_pipeline_task.run(
            "sched-1",
            triggered_by="catchup",
            scheduled_for="2026-04-17T10:00:00+00:00",
        )

    assert captured.get("scheduled_for") == "2026-04-17T10:00:00+00:00", (
        "Il task non ha propagato scheduled_for all'ExecutionManager — "
        "il kwarg e' ombra morta."
    )
