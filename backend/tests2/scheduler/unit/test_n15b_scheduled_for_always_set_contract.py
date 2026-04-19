"""Contract test N15b — scheduled_for sempre valorizzato in schedule_pipeline_task.

Chiude il residuo di N15: i cron "normali" (beat_scheduler.py:502, 522)
emettono `schedule_pipeline_task(schedule_id, triggered_by="cron")` SENZA
scheduled_for. Stessa cosa per manuale (scheduler/router.py:345).

Per la chiave idempotency di CARICA (N15), `scheduled_for=None` significa
no protezione: run_id=None -> guard executor no-op -> retry puo' duplicare
su write_mode=append.

Fix: nel task stesso, se `scheduled_for is None`, calcolo `now_iso()` al
task entry. Il retry propaga gia' scheduled_for via retry kwargs (fix N15
in execution_manager._handle_failure), quindi il valore calcolato resta
stabile attraverso retry di quella invocazione.

Copertura:
1. Task con scheduled_for esplicito: lo preserva intatto
2. Task con scheduled_for=None: calcola valore ISO8601 UTC
3. Due chiamate senza scheduled_for in momenti diversi: ISO diversi
   (ogni invocazione e' distinguibile — manuale Esegui Ora multipli OK)
4. Sentinel: source del task contiene la computation di fallback
"""

from __future__ import annotations

import inspect
import re
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _task_source() -> str:
    """Restituisce il source raw di schedule_pipeline_task."""
    from workers.tasks import schedule_task
    return inspect.getsource(schedule_task.schedule_pipeline_task)


# =============================================================================
# 1. Sentinel source: il task contiene il fallback now_iso quando None
# =============================================================================


def test_task_source_has_scheduled_for_fallback_logic():
    """Il task deve contenere logica che valorizzi scheduled_for quando None.
    Pattern accettabile: 'if scheduled_for is None' + datetime now/utcnow.
    """
    src = _task_source()
    assert "scheduled_for is None" in src or "scheduled_for = scheduled_for or" in src, (
        "Task non ha fallback per scheduled_for=None — N15b gap aperto."
    )
    # Deve referenziare qualche forma di 'now' + iso + timezone-aware
    has_now = "datetime.now" in src or "datetime.utcnow" in src
    assert has_now, "Fallback non computa un timestamp 'now'."


# =============================================================================
# 2. Behavior: scheduled_for esplicito -> preservato
# =============================================================================


def test_task_preserves_explicit_scheduled_for():
    """Se il caller passa scheduled_for, non deve essere sovrascritto.
    Testiamo indirettamente via ExecutionManager mock."""
    from workers.tasks import schedule_task

    captured = {}

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

    assert captured.get("scheduled_for") == "2026-04-17T10:00:00+00:00"


# =============================================================================
# 3. Behavior: scheduled_for None + cron -> compute now
# =============================================================================


def test_task_computes_scheduled_for_when_none_for_cron():
    """Con triggered_by='cron' e scheduled_for=None, il task deve computare
    un ISO timestamp prima di passarlo a ExecutionManager.execute."""
    from workers.tasks import schedule_task

    captured = {}

    class _MockManager:
        def execute(self, **kwargs):
            captured.update(kwargs)
            return {"success": True, "status": "completed"}

    before = datetime.now(timezone.utc)
    with patch(
        "app.core.scheduler.execution_manager.ExecutionManager",
        return_value=_MockManager(),
    ):
        schedule_task.schedule_pipeline_task.run(
            "sched-1",
            triggered_by="cron",
            # NO scheduled_for
        )
    after = datetime.now(timezone.utc)

    sf = captured.get("scheduled_for")
    assert sf is not None, "scheduled_for resta None -> N15b non chiuso."
    # Deve essere un ISO parsabile e vicino al now
    parsed = datetime.fromisoformat(sf)
    assert before <= parsed <= after, (
        f"scheduled_for calcolato ({sf}) non nell'intervallo [{before}, {after}]"
    )


def test_task_computes_scheduled_for_when_none_for_manual():
    """Idem per triggered_by='manual'."""
    from workers.tasks import schedule_task

    captured = {}

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
            triggered_by="manual",
        )

    assert captured.get("scheduled_for") is not None


# =============================================================================
# 4. Distinguibilita' di invocazioni diverse
# =============================================================================


def test_two_cron_invocations_without_scheduled_for_get_different_values():
    """Due cron fire consecutivi devono ottenere ISO distinti (microsecondi
    nell'ISO format). Garantisce che run_id di CARICA sia distinto per tick
    diversi — click Esegui Ora multipli producono run separate."""
    from workers.tasks import schedule_task
    import time

    captured = []

    class _MockManager:
        def execute(self, **kwargs):
            captured.append(kwargs.get("scheduled_for"))
            return {"success": True, "status": "completed"}

    with patch(
        "app.core.scheduler.execution_manager.ExecutionManager",
        return_value=_MockManager(),
    ):
        schedule_task.schedule_pipeline_task.run("s", triggered_by="cron")
        time.sleep(0.002)  # garantisce differenza a microsecondi
        schedule_task.schedule_pipeline_task.run("s", triggered_by="cron")

    assert len(captured) == 2
    assert captured[0] != captured[1], (
        "Due invocazioni cron consecutive hanno prodotto lo stesso scheduled_for. "
        "Il fallback ha risoluzione insufficiente."
    )
