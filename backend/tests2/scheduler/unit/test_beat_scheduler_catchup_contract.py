"""
Orchestration del catchup nel beat scheduler (C3 fix 3).

Contratto `_emit_catchup_runs()`:
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from workers.beat_scheduler import PlenoraBeatScheduler


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


class _FakeScheduler(PlenoraBeatScheduler):
    """Scheduler senza bootstrap Celery + app.send_task mockato."""

    def __init__(self):
        self._db_loaded = False
        self.data = {}
        self.app = MagicMock()
        self.app.now = MagicMock(return_value=MagicMock(name="now"))
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


def _schedule_row(
    *,
    sid: str = "sched-1",
    cron: str = "0 6 * * *",
    last_run: str = "2026-04-15T06:00:00+00:00",
    catchup_enabled: bool = True,
    grace_hours: int = 24,
) -> dict:
    """Builder per riga DB schedules."""
    config = {
        "schedule": {
            "cron_expression": cron,
            "catchup_missed_runs": catchup_enabled,
            "catchup_grace_hours": grace_hours,
        }
    }
    return {
        "id": sid,
        "cron_expression": cron,
        "last_run": last_run,
        "config": json.dumps(config),
        "is_enabled": True,
    }


# Fixed `now` compatible con last_run "ieri 06:00" → expected_last = oggi 06:00
_NOW_FIXED = datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc)


# Skip quando flag catchup disabled


def test_skips_schedule_when_catchup_flag_disabled(scheduler):
    row = _schedule_row(catchup_enabled=False)

    with patch("app.db.pg_connection.fetchall", return_value=[row]), \
         patch("app.db.pg_connection.execute"), \
         patch("workers.beat_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW_FIXED
        mock_dt.fromisoformat = datetime.fromisoformat
        scheduler._emit_catchup_runs()

    scheduler.app.send_task.assert_not_called()


def test_skips_schedule_when_disabled_globally(scheduler):
    """Anche se catchup flag e' on, schedule disabilitato non emette."""
    row = _schedule_row(catchup_enabled=True)
    row["is_enabled"] = False

    with patch("app.db.pg_connection.fetchall", return_value=[]) as ff, \
         patch("app.db.pg_connection.execute"), \
         patch("workers.beat_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW_FIXED
        mock_dt.fromisoformat = datetime.fromisoformat
        scheduler._emit_catchup_runs()

    # La query deve filtrare is_enabled=TRUE, quindi la riga non torna
    scheduler.app.send_task.assert_not_called()
    # Check che la query filtri correttamente
    ff.assert_called_once()
    query = ff.call_args[0][0]
    assert "is_enabled" in query.lower()


# Emit task con kwargs corretti


def test_emits_task_with_triggered_by_catchup_and_scheduled_for(scheduler):
    row = _schedule_row(
        sid="sched-1",
        cron="0 6 * * *",
        last_run="2026-04-15T06:00:00+00:00",
    )

    with patch("app.db.pg_connection.fetchall", return_value=[row]), \
         patch("app.db.pg_connection.execute"), \
         patch("workers.beat_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW_FIXED
        mock_dt.fromisoformat = datetime.fromisoformat
        scheduler._emit_catchup_runs()

    scheduler.app.send_task.assert_called_once()
    call_kwargs = scheduler.app.send_task.call_args
    task_name = call_kwargs[0][0]
    kwargs = call_kwargs.kwargs.get("kwargs", {})

    assert task_name == "workers.tasks.schedule_task.schedule_pipeline_task"
    assert kwargs["schedule_id"] == "sched-1"
    assert kwargs["triggered_by"] == "catchup"
    # scheduled_for deve essere il timestamp dell'expected_last
    assert kwargs["scheduled_for"] == "2026-04-16T06:00:00+00:00"


# Update last_run per prevenire re-trigger


# Audit history


def test_writes_audit_entry_in_schedule_catchup_history(scheduler):
    row = _schedule_row(sid="sched-1", last_run="2026-04-15T06:00:00+00:00")

    executed_queries = []

    def capture_execute(query, params=None):
        executed_queries.append((query, params))

    with patch("app.db.pg_connection.fetchall", return_value=[row]), \
         patch("app.db.pg_connection.execute", side_effect=capture_execute), \
         patch("workers.beat_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW_FIXED
        mock_dt.fromisoformat = datetime.fromisoformat
        scheduler._emit_catchup_runs()

    audit_calls = [
        (q, p) for q, p in executed_queries if "schedule_catchup_history" in q.lower()
    ]
    assert audit_calls, "nessuna INSERT su schedule_catchup_history"
    _q, params = audit_calls[0]
    params_str = str(params)
    assert "sched-1" in params_str
    assert "2026-04-16T06:00:00+00:00" in params_str  # scheduled_for
    assert "beat-test@host" in params_str              # leader_id


# Cap catchup consecutivi


def test_cap_exceeded_skips_emit_and_logs_warning(scheduler, caplog):
    """Se uno schedule ha gia' >= 5 catchup emessi nei precedenti 10 min
    (lookup su schedule_catchup_history), skip del catchup + WARNING."""
    import logging

    row = _schedule_row(sid="sched-hot", last_run="2026-04-15T06:00:00+00:00")

    # Mock fetchall per:
    #   1. SELECT schedules WHERE catchup_missed_runs → [row]
    #   2. SELECT COUNT FROM schedule_catchup_history WHERE ... → 5 (cap raggiunto)
    fetchall_responses = [[row], [{"count": 5}]]

    def fake_fetchall(*args, **kwargs):
        return fetchall_responses.pop(0)

    with patch("app.db.pg_connection.fetchall", side_effect=fake_fetchall), \
         patch("app.db.pg_connection.execute"), \
         patch("workers.beat_scheduler.datetime") as mock_dt, \
         caplog.at_level(logging.WARNING, logger="workers.beat_scheduler"):
        mock_dt.now.return_value = _NOW_FIXED
        mock_dt.fromisoformat = datetime.fromisoformat
        scheduler._emit_catchup_runs()

    scheduler.app.send_task.assert_not_called()
    assert any("CATCHUP" in rec.getMessage().upper() for rec in caplog.records)


def test_cap_not_exceeded_still_emits(scheduler):
    """Se ci sono < 5 catchup nei 10min precedenti, procede normalmente."""
    row = _schedule_row(sid="sched-ok", last_run="2026-04-15T06:00:00+00:00")

    fetchall_responses = [[row], [{"count": 2}]]

    def fake_fetchall(*args, **kwargs):
        return fetchall_responses.pop(0)

    with patch("app.db.pg_connection.fetchall", side_effect=fake_fetchall), \
         patch("app.db.pg_connection.execute"), \
         patch("workers.beat_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW_FIXED
        mock_dt.fromisoformat = datetime.fromisoformat
        scheduler._emit_catchup_runs()

    scheduler.app.send_task.assert_called_once()


# Robustezza a DB error


def test_db_error_on_main_query_is_swallowed(scheduler, caplog):
    """Un errore DB nel fetch degli schedule non deve crashare il tick."""
    import logging

    with patch("app.db.pg_connection.fetchall", side_effect=RuntimeError("db down")), \
         caplog.at_level(logging.ERROR, logger="workers.beat_scheduler"):
        scheduler._emit_catchup_runs()  # non deve sollevare

    scheduler.app.send_task.assert_not_called()


def test_emits_nothing_when_no_missed_run_detected(scheduler):
    """last_run recente → detect_missed_run ritorna None → no emit."""
    # last_run = oggi alle 06:00, now = oggi alle 10:00 → non c'e' missed
    row = _schedule_row(sid="s1", last_run="2026-04-16T06:00:00+00:00")

    with patch("app.db.pg_connection.fetchall", return_value=[row]), \
         patch("app.db.pg_connection.execute"), \
         patch("workers.beat_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW_FIXED
        mock_dt.fromisoformat = datetime.fromisoformat
        scheduler._emit_catchup_runs()

    scheduler.app.send_task.assert_not_called()
