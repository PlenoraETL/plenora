"""
Caratterizzazione di PlenoraBeatScheduler.tick() e _sync_from_db().

Scopo: cristallizzare il comportamento ATTUALE del beat scheduler
"""

from unittest.mock import MagicMock, patch

import pytest
from celery.beat import Scheduler

from workers.beat_scheduler import PlenoraBeatScheduler


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


class _FakeScheduler(PlenoraBeatScheduler):
    """PlenoraBeatScheduler senza bootstrap Celery (test unit)."""

    def __init__(self):
        # Salta super().__init__ (richiederebbe Celery app reale).
        # Imposta solo gli attributi che i metodi testati toccano.
        self._db_loaded = False
        self.data = {}
        self.app = MagicMock()
        self.app.now = MagicMock(return_value=MagicMock(name="now"))
        self._last_stuck_check = 0.0
        self._last_eseguibili_cleanup = 0.0
        self._last_logs_retention = 0.0
        self._last_imdf_bundles_cleanup = 0.0
        # Attributi per leader election (C3 fix 1). Qui i test di
        # caratterizzazione del sync vogliono il behavior post-leader,
        # quindi nei singoli test patchiamo _try_become_leader → True.
        self._leader_id = "test-beat@host"
        self._is_leader = True
        self._redis_client = None


@pytest.fixture
def scheduler():
    return _FakeScheduler()


# tick() — delega a super + chiama sync + heartbeat + 4 cleanup


def test_tick_calls_sync_heartbeat_and_all_cleanups(scheduler):
    """tick() del leader deve orchestrare: _sync_from_db,
    _write_heartbeat, i 4 cleanup jobs, poi delegare a super().tick()."""
    with patch.object(scheduler, "_try_become_leader", return_value=True), \
         patch.object(scheduler, "_sync_from_db") as sync, \
         patch.object(scheduler, "_write_heartbeat") as heartbeat, \
         patch.object(scheduler, "_check_stuck_tasks") as stuck, \
         patch.object(scheduler, "_cleanup_eseguibili_outputs") as outputs, \
         patch.object(scheduler, "_cleanup_imdf_bundles") as bundles, \
         patch.object(scheduler, "_cleanup_old_logs") as logs, \
         patch.object(Scheduler, "tick", return_value=42.0) as super_tick:
        result = scheduler.tick()

    sync.assert_called_once()
    heartbeat.assert_called_once()
    stuck.assert_called_once()
    outputs.assert_called_once()
    bundles.assert_called_once()
    logs.assert_called_once()
    super_tick.assert_called_once()
    assert result == 42.0


def test_tick_delegates_event_t_to_super_when_provided(scheduler):
    """Se Celery passa event_t, tick() (da leader) lo propaga a super().tick()."""
    with patch.object(scheduler, "_try_become_leader", return_value=True), \
         patch.object(scheduler, "_sync_from_db"), \
         patch.object(scheduler, "_write_heartbeat"), \
         patch.object(scheduler, "_check_stuck_tasks"), \
         patch.object(scheduler, "_cleanup_eseguibili_outputs"), \
         patch.object(scheduler, "_cleanup_imdf_bundles"), \
         patch.object(scheduler, "_cleanup_old_logs"), \
         patch.object(Scheduler, "tick", return_value=1.0) as super_tick:
        scheduler.tick(event_t=("tick", 100, object()))

    super_tick.assert_called_once()
    _args, kwargs = super_tick.call_args
    assert kwargs.get("event_t") == ("tick", 100, super_tick.call_args.kwargs["event_t"][2])


# _sync_from_db — carica schedule dal DB in self.data


def test_sync_from_db_adds_entry_for_enabled_schedule(scheduler):
    """Una riga schedules enabled deve diventare una ScheduleEntry in self.data."""
    rows = [
        {
            "id": "sched-1",
            "name": "Daily 6am",
            "cron_expression": "0 6 * * *",
            "project_id": "proj-1",
            "is_enabled": True,
            "last_run": None,
        }
    ]
    with patch("app.db.pg_connection.fetchall", return_value=rows):
        scheduler._sync_from_db()

    assert "urby:schedule:sched-1" in scheduler.data
    entry = scheduler.data["urby:schedule:sched-1"]
    assert entry.task == "workers.tasks.schedule_task.schedule_pipeline_task"
    assert entry.kwargs == {"schedule_id": "sched-1", "triggered_by": "cron"}


def test_sync_from_db_removes_disabled_schedule(scheduler):
    """Se uno schedule precedentemente in self.data non e' piu' nelle
    righe enabled del DB, deve essere rimosso da self.data."""
    # Prima pass: sched-1 enabled
    with patch("app.db.pg_connection.fetchall", return_value=[
        {"id": "sched-1", "name": "A", "cron_expression": "0 * * * *",
         "project_id": "p1", "is_enabled": True, "last_run": None}
    ]):
        scheduler._sync_from_db()

    assert "urby:schedule:sched-1" in scheduler.data

    # Seconda pass: sched-1 NON e' piu' nelle righe enabled (disabilitato)
    with patch("app.db.pg_connection.fetchall", return_value=[]):
        scheduler._sync_from_db()

    assert "urby:schedule:sched-1" not in scheduler.data


def test_sync_from_db_skips_invalid_cron_expression(scheduler):
    """Una cron malformata non deve crashare il sync — row skippata."""
    rows = [
        {"id": "bad", "name": "Bad", "cron_expression": "not a cron",
         "project_id": "p", "is_enabled": True, "last_run": None}
    ]
    with patch("app.db.pg_connection.fetchall", return_value=rows):
        scheduler._sync_from_db()

    assert "urby:schedule:bad" not in scheduler.data


def test_sync_from_db_is_resilient_to_db_error(scheduler):
    """Un errore DB non deve crashare il tick (viene loggato e
    swallowed). self.data resta inalterato."""
    scheduler.data["urby:schedule:pre-esistente"] = MagicMock()

    with patch("app.db.pg_connection.fetchall", side_effect=RuntimeError("db down")):
        scheduler._sync_from_db()  # non deve sollevare

    assert "urby:schedule:pre-esistente" in scheduler.data


def test_sync_from_db_updates_entry_when_cron_changes(scheduler):
    """Se la cron di uno schedule cambia tra due sync, l'entry in
    self.data deve essere aggiornata con la nuova schedule."""
    with patch("app.db.pg_connection.fetchall", return_value=[
        {"id": "s1", "name": "A", "cron_expression": "0 6 * * *",
         "project_id": "p", "is_enabled": True, "last_run": None}
    ]):
        scheduler._sync_from_db()

    old_schedule = scheduler.data["urby:schedule:s1"].schedule

    with patch("app.db.pg_connection.fetchall", return_value=[
        {"id": "s1", "name": "A", "cron_expression": "*/15 * * * *",
         "project_id": "p", "is_enabled": True, "last_run": None}
    ]):
        scheduler._sync_from_db()

    new_schedule = scheduler.data["urby:schedule:s1"].schedule
    assert str(old_schedule) != str(new_schedule)


# _parse_cron — validazione espressione cron


def test_parse_cron_accepts_standard_5_field_expression(scheduler):
    result = scheduler._parse_cron("0 6 * * *")
    assert result is not None


def test_parse_cron_rejects_wrong_number_of_fields(scheduler):
    # 4 campi (manca day_of_week)
    assert scheduler._parse_cron("0 6 * *") is None
    # 6 campi (seconds non supportato)
    assert scheduler._parse_cron("0 0 6 * * *") is None


def test_parse_cron_rejects_empty_string(scheduler):
    assert scheduler._parse_cron("") is None
