"""Catchup non avanza last_run prima del successo del task (C3 follow-up).

Fix 2 del follow-up: l'emit del task catchup NON aggiorna last_run
immediatamente. Se il task crasha o non viene mai picked-up dal
worker, al tick successivo detect_missed_run deve ancora vedere il
missed e poter ri-emettere.

Anti-loop: dedupe via schedule_catchup_history. Se uno stesso
`scheduled_for` e' stato gia' emesso nelle ultime 2h, skip senza
ri-emettere (evita spam di task duplicati finche' il primo e' in
volo nella coda Celery). Dopo 2h, l'entry e' considerata stale e
il re-emit e' permesso (il task era molto probabilmente fallito).
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from workers.beat_scheduler import PlenoraBeatScheduler


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


class _FakeScheduler(PlenoraBeatScheduler):
    def __init__(self):
        self._db_loaded = False
        self.data = {}
        self.app = MagicMock()
        self.app.send_task = MagicMock(return_value=MagicMock(id="task-xyz"))
        self._last_stuck_check = 0.0
        self._last_eseguibili_cleanup = 0.0
        self._last_logs_retention = 0.0
        self._last_imdf_bundles_cleanup = 0.0
        self._leader_id = "beat-test@host"
        self._is_leader = True
        self._redis_client = None


@pytest.fixture
def scheduler():
    return _FakeScheduler()


def _schedule_row(sid="sched-1", cron="0 6 * * *",
                  last_run="2026-04-15T06:00:00+00:00", catchup_enabled=True):
    config = {
        "schedule": {
            "cron_expression": cron,
            "catchup_missed_runs": catchup_enabled,
            "catchup_grace_hours": 24,
        }
    }
    return {
        "id": sid, "cron_expression": cron, "last_run": last_run,
        "config": json.dumps(config), "is_enabled": True,
    }


_NOW_FIXED = datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc)


# =============================================================================
# Fix 2a: last_run NON aggiornato al emit
# =============================================================================


def test_emit_catchup_does_not_update_last_run_to_expected(scheduler):
    """Contract: _emit_single_catchup emette il task ma NON fa
    UPDATE schedules SET last_run=... — quello spetta al task al suo
    successo. Se il task fallisce, al tick successivo detect_missed_run
    deve ancora vedere il missed."""
    row = _schedule_row(sid="sched-1", last_run="2026-04-15T06:00:00+00:00")
    executed_queries = []

    def capture(query, params=None):
        executed_queries.append((query, params))

    with patch("app.db.db_service.fetchall",
               side_effect=[[row], [{"count": 0}]]), \
         patch("app.db.db_service.execute", side_effect=capture), \
         patch("workers.beat_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW_FIXED
        mock_dt.fromisoformat = datetime.fromisoformat
        scheduler._emit_catchup_runs()

    # Emit avvenuto
    scheduler.app.send_task.assert_called_once()
    # MA nessuna UPDATE last_run
    update_last_run = [
        q for q, _ in executed_queries
        if "update" in q.lower() and "last_run" in q.lower()
    ]
    assert update_last_run == [], (
        f"UPDATE last_run avvenuta prematuramente: {update_last_run}"
    )


# =============================================================================
# Fix 2b: dedupe via history (skip se gia' emesso per lo stesso scheduled_for)
# =============================================================================


def test_skips_reemit_when_recent_catchup_for_same_scheduled_for_exists(scheduler):
    """Se `schedule_catchup_history` ha gia' una riga con lo stesso
    scheduled_for emessa nelle ultime 2h, _emit_catchup_runs NON
    deve re-emettere un duplicato. Evita spam finche' il primo task
    e' in volo nella coda Celery."""
    row = _schedule_row(sid="sched-1", last_run="2026-04-15T06:00:00+00:00")
    # expected_last per cron "0 6 * * *" con now=10:00 del 16 → 2026-04-16 06:00
    # In history c'e' gia' una riga emessa 30 minuti fa per quel scheduled_for
    history_query_responses = [
        [row],                           # main query
        [{"count": 0}],                  # runaway count OK
        [{"count": 1}],                  # dedupe: gia' 1 emit per stesso scheduled_for
    ]

    with patch("app.db.db_service.fetchall",
               side_effect=history_query_responses), \
         patch("app.db.db_service.execute"), \
         patch("workers.beat_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW_FIXED
        mock_dt.fromisoformat = datetime.fromisoformat
        scheduler._emit_catchup_runs()

    scheduler.app.send_task.assert_not_called()


def test_reemits_when_previous_catchup_is_stale(scheduler):
    """Se l'ultima emit per lo stesso scheduled_for e' piu' vecchia
    di 2h (task quasi certamente morto), consenti il re-emit."""
    row = _schedule_row(sid="sched-1", last_run="2026-04-15T06:00:00+00:00")
    history_query_responses = [
        [row],
        [{"count": 0}],    # runaway OK
        [{"count": 0}],    # dedupe: nessun emit recente per stesso scheduled_for
    ]

    with patch("app.db.db_service.fetchall",
               side_effect=history_query_responses), \
         patch("app.db.db_service.execute"), \
         patch("workers.beat_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW_FIXED
        mock_dt.fromisoformat = datetime.fromisoformat
        scheduler._emit_catchup_runs()

    scheduler.app.send_task.assert_called_once()


# =============================================================================
# Fix 2c: audit insert sempre fatto (per il dedupe al tick successivo)
# =============================================================================


def test_emit_catchup_still_writes_audit_history_entry(scheduler):
    """Il dedupe si basa sulla history → quindi ogni emit deve
    inserire la sua riga in schedule_catchup_history, anche senza
    aggiornare last_run."""
    row = _schedule_row(sid="sched-1", last_run="2026-04-15T06:00:00+00:00")
    executed = []

    def capture(query, params=None):
        executed.append((query, params))

    with patch("app.db.db_service.fetchall",
               side_effect=[[row], [{"count": 0}], [{"count": 0}]]), \
         patch("app.db.db_service.execute", side_effect=capture), \
         patch("workers.beat_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW_FIXED
        mock_dt.fromisoformat = datetime.fromisoformat
        scheduler._emit_catchup_runs()

    audit_inserts = [
        q for q, _ in executed
        if "schedule_catchup_history" in q.lower() and "insert" in q.lower()
    ]
    assert len(audit_inserts) == 1
