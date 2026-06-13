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
    """tick() del leader deve orchestrare: _sync_from_db, _write_heartbeat,
    i cleanup jobs, poi emettere gli schedule dovuti via dispatch (F3.2:
    rimpiazza super().tick() di Celery)."""
    with patch.object(scheduler, "_try_become_leader", return_value=True), \
         patch.object(scheduler, "_sync_from_db") as sync, \
         patch.object(scheduler, "_write_heartbeat") as heartbeat, \
         patch.object(scheduler, "_check_stuck_tasks") as stuck, \
         patch.object(scheduler, "_cleanup_eseguibili_outputs") as outputs, \
         patch.object(scheduler, "_cleanup_old_logs") as logs, \
         patch.object(scheduler, "_emit_due_via_dispatch", return_value=42.0) as emit:
        result = scheduler.tick()

    sync.assert_called_once()
    heartbeat.assert_called_once()
    stuck.assert_called_once()
    outputs.assert_called_once()
    logs.assert_called_once()
    emit.assert_called_once()
    assert result == 42.0


def test_tick_accepts_event_t_from_celery_loop(scheduler):
    """Finche' il loop e' Celery beat (pre-cutover APScheduler), tick() puo'
    ricevere event_t: deve accettarlo senza crash ed emettere comunque."""
    with patch.object(scheduler, "_try_become_leader", return_value=True), \
         patch.object(scheduler, "_sync_from_db"), \
         patch.object(scheduler, "_write_heartbeat"), \
         patch.object(scheduler, "_check_stuck_tasks"), \
         patch.object(scheduler, "_cleanup_eseguibili_outputs"), \
         patch.object(scheduler, "_cleanup_old_logs"), \
         patch.object(scheduler, "_emit_due_via_dispatch", return_value=1.0) as emit:
        result = scheduler.tick(event_t=("tick", 100, object()))

    emit.assert_called_once()
    assert result == 1.0


class _FakeEntry:
    """Minimo ScheduleEntry: is_due() + __next__() che avanza."""

    def __init__(self, due, task="workers.tasks.schedule_task.schedule_pipeline_task",
                 kwargs=None, next_time=30.0):
        self._due = due
        self.task = task
        self.kwargs = kwargs or {}
        self._next = next_time
        self.advanced = False

    def is_due(self):
        return (self._due, self._next)

    def __next__(self):
        self.advanced = True
        return self


def test_emit_due_via_dispatch_dispatches_only_due_entries(scheduler):
    due = _FakeEntry(True, kwargs={"schedule_id": "s1", "triggered_by": "cron"})
    not_due = _FakeEntry(False, kwargs={"schedule_id": "s2"})
    scheduler.data = {"urby:schedule:s1": due, "urby:schedule:s2": not_due}

    with patch.object(scheduler, "_dispatch_schedule_task") as dispatch:
        result = scheduler._emit_due_via_dispatch()

    dispatch.assert_called_once_with(
        "workers.tasks.schedule_task.schedule_pipeline_task",
        {"schedule_id": "s1", "triggered_by": "cron"},
    )
    # Solo l'entry dovuta avanza last_run_at; l'altra resta invariata.
    assert due.advanced is True
    assert not_due.advanced is False
    # Sleep = min(max_interval, next_time delle entry).
    assert result == 30.0


def test_emit_due_via_dispatch_clamps_to_max_interval(scheduler):
    far = _FakeEntry(False, next_time=99999.0)
    scheduler.data = {"urby:schedule:s1": far}

    with patch.object(scheduler, "_dispatch_schedule_task"):
        result = scheduler._emit_due_via_dispatch()

    assert result == scheduler.max_interval


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
    assert entry.kwargs == {
        "schedule_id": "sched-1",
        "triggered_by": "cron",
    }


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
