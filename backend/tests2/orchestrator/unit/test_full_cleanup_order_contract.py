"""UI conferma finale F8/G3 (06/11/2026): prima l'annullamento, poi la pulizia.

Prima l'esecutore chiudendo la scheda mandava in parallelo l'annullamento e la
pulizia completa: quello che il worker scriveva dopo la pulizia restava. Ora
una sola chiamata (full-cleanup) e una regola (`task_cleanup.cleanup_task`):

- la pulizia tocca solo un task terminale (la funzione che pulisce lo rifiuta);
- un task in corso prima si annulla, poi si aspetta (tetto dichiarato);
- se non si ferma la pulizia va in coda (202) e la fa lo sweep del beat
  appena il task e' terminale, o dopo l'attesa massima;
- uno sweep con passi falliti tiene la riga e la riprova.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.config import Settings
from app.core.orchestrator import task_cleanup
from app.core.orchestrator import task_service as task_service_module

pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.contract]


def _info(status: str, *, project_id: str = "p1", result: dict | None = None):
    return SimpleNamespace(
        status=status,
        project_id=project_id,
        result=result,
        subject_kind="project",
        subject_id=project_id,
        group_id="grp-1",
        owner_user_id=None,
    )


class _Recorder:
    """Registra in ordine annullamento, letture di stato e passi di pulizia."""

    def __init__(self, statuses: list[str]):
        self.events: list[str] = []
        self._statuses = list(statuses)

    def cancel(self, task_id):
        self.events.append("cancel")
        return True

    def status(self, task_id):
        self.events.append("status")
        st = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        return _info(st, result={"project_id": "p1", "result_id": "res-1"} if st in task_cleanup.TERMINAL_STATUSES else None)

    def step(self, name):
        def _do(*_a, **_k):
            self.events.append(name)
            return True
        return _do


def _patched(rec: _Recorder):
    svc = task_service_module.task_service
    return [
        patch.object(svc, "cancel_task", side_effect=rec.cancel),
        patch.object(svc, "get_task_status", side_effect=rec.status),
        patch.object(svc, "forget_task_result", side_effect=rec.step("forget")),
        patch("app.core.results.cleanup.delete_result", side_effect=rec.step("delete_result")),
        patch("app.core.orchestrator.log_cleanup.delete_task_log_runs", side_effect=rec.step("delete_logs")),
        patch.object(task_cleanup, "defer_cleanup", side_effect=lambda tid, **k: rec.events.append("defer")),
        patch.object(task_cleanup, "forget_deferred_cleanup", side_effect=lambda tid: rec.events.append("dequeue")),
    ]


def _run(rec, fn):
    patches = _patched(rec)
    for p in patches:
        p.start()
    try:
        return fn()
    finally:
        for p in reversed(patches):
            p.stop()


PURGE_STEPS = {"delete_result", "delete_logs", "forget"}


def test_purge_refuses_a_task_that_is_not_terminal():
    for status in ("pending", "started", "progress", "retry", None):
        with pytest.raises(RuntimeError):
            task_cleanup.purge_terminal_task("t", _info(status))


def test_terminal_task_is_cleaned_at_once_without_cancelling():
    rec = _Recorder(["success"])
    out = _run(rec, lambda: task_cleanup.cleanup_task("t", _info("success", result={"result_id": "res-1"})))
    assert out.deferred is False and out.failed_steps == ()
    assert "cancel" not in rec.events
    assert PURGE_STEPS <= set(rec.events)


def test_running_task_is_cancelled_first_and_cleaned_only_once_terminal():
    rec = _Recorder(["started", "started", "revoked"])
    clock = iter(range(100))
    out = _run(rec, lambda: task_cleanup.cleanup_task(
        "t", _info("started"), wait_seconds=10, sleep=lambda s: None, clock=lambda: next(clock)))
    assert out.deferred is False
    first_purge = min(rec.events.index(s) for s in PURGE_STEPS)
    assert rec.events[0] == "cancel"
    # Nessun passo di pulizia prima dell'ultima lettura (quella terminale).
    last_status = max(i for i, e in enumerate(rec.events) if e == "status")
    assert first_purge > last_status


def test_task_that_does_not_stop_is_queued_and_nothing_is_purged():
    rec = _Recorder(["progress"])
    clock = iter(range(0, 1000, 5))
    out = _run(rec, lambda: task_cleanup.cleanup_task(
        "t", _info("progress"), wait_seconds=10, sleep=lambda s: None, clock=lambda: next(clock)))
    assert out.deferred is True
    assert rec.events[0] == "cancel" and rec.events[-1] == "defer"
    assert not PURGE_STEPS & set(rec.events)


def test_endpoint_answers_202_for_a_queued_cleanup_and_never_cancels_after_cleaning(client):
    rec = _Recorder(["progress"])
    with patch.object(task_cleanup, "_setting_seconds", return_value=0), \
         patch("app.core.projects.service.ProjectService.get_project_metadata", return_value={"id": "p1", "group_id": "grp-1"}):
        response = _run(rec, lambda: client.delete("/api/tasks/t-run/full-cleanup"))
    assert response.status_code == 202
    assert response.json() == {"status": "deferred", "task_id": "t-run"}
    assert "cancel" in rec.events and not PURGE_STEPS & set(rec.events)


def test_endpoint_cleans_a_finished_task_with_204(client, tmp_path):
    rec = _Recorder(["success"])
    with patch("app.core.projects.service.ProjectService.get_project_metadata", return_value={"id": "p1", "group_id": "grp-1"}), \
         patch("app.core.orchestrator.task_cleanup.settings", new=SimpleNamespace(RESULTS_DIR=tmp_path)):
        response = _run(rec, lambda: client.delete("/api/tasks/t-done/full-cleanup"))
    assert response.status_code == 204
    assert "cancel" not in rec.events and PURGE_STEPS <= set(rec.events)


def test_sweep_cleans_terminal_waits_running_and_forces_overdue():
    statuses = {"t-done": "failure", "t-run": "started", "t-old": "started"}
    rows = [{"task_id": "t-done", "overdue": False}, {"task_id": "t-run", "overdue": False},
            {"task_id": "t-old", "overdue": True}]
    purged: list[tuple[str, str]] = []
    dequeued: list[str] = []
    svc = task_service_module.task_service
    with patch.object(task_cleanup, "deferred_cleanups", return_value=rows), \
         patch.object(svc, "get_task_status", side_effect=lambda tid: _info(statuses[tid])), \
         patch.object(task_cleanup, "purge_terminal_task", side_effect=lambda tid, info: purged.append((tid, info.status)) or []), \
         patch.object(task_cleanup, "forget_deferred_cleanup", side_effect=dequeued.append):
        out = task_cleanup.run_deferred_cleanups()
    assert out == {"done": 2, "waiting": 1, "failed": 0}
    assert purged == [("t-done", "failure"), ("t-old", "revoked")]
    assert dequeued == ["t-done", "t-old"]


def test_sweep_keeps_a_failed_cleanup_for_the_next_round():
    svc = task_service_module.task_service
    attempts: list[tuple] = []
    with patch.object(task_cleanup, "deferred_cleanups", return_value=[{"task_id": "t", "overdue": False}]), \
         patch.object(svc, "get_task_status", return_value=_info("success")), \
         patch.object(task_cleanup, "purge_terminal_task", return_value=["delete_log_runs"]), \
         patch.object(task_cleanup, "_record_attempt", side_effect=lambda *a: attempts.append(a)), \
         patch.object(task_cleanup, "forget_deferred_cleanup") as forget:
        out = task_cleanup.run_deferred_cleanups()
    assert out["failed"] == 1 and attempts == [("t", ["delete_log_runs"])]
    forget.assert_not_called()


def test_waits_are_declared_in_config_defaults_and_the_sweep_is_scheduled():
    from app.core.scheduler.beat_schedule_config import beat_schedule_config

    assert Settings.DEFAULTS["TASK_CLEANUP_CANCEL_WAIT_SECONDS"] > 0
    assert Settings.DEFAULTS["TASK_CLEANUP_DEFER_MAX_SECONDS"] >= 3 * 60 * 60
    entry = beat_schedule_config()["task-cleanup-deferred-minutely"]
    assert entry["task"] == "workers.tasks.task_cleanup_task.run"


def test_the_router_has_no_purge_steps_of_its_own():
    """La pulizia e' solo in task_cleanup (una regola, un posto)."""
    import inspect

    from app.core.orchestrator import router

    src = inspect.getsource(router.cleanup_task_completely)
    assert "task_cleanup.cleanup_task(" in src
    for step in ("delete_result(", "delete_task_log_runs(", "forget_task_result(", "delete_task_outputs("):
        assert step not in src
