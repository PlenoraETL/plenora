"""Pulizia completa di un task (full-cleanup), dopo la semplificazione H1–H7.

Regola unica in `task_cleanup` (docs/audits/ui-conferma-finale-2026-11-06.md,
giro su c916dc42): niente coda ne' sweep.

- niente da pulire (nessun record, nessun metadato) -> 204 subito, senza
  annullare ne' leggere lo stato (H2);
- task terminale -> pulito ora; record e metadati si tolgono per ultimi e solo
  se tutti i passi sui dati sono riusciti, quindi una ripetizione dopo un 207
  rifa' i passi mancanti col payload vero (H1);
- task in corso -> richiesta annotata sul task PRIMA di rileggere lo stato,
  annullamento, 202 senza attesa; la pulizia la fa chi porta il task allo
  stato terminale (worker a fine task, beat per un worker morto).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.orchestrator import task_cleanup
from app.core.orchestrator import task_service as task_service_module
from app.core.orchestrator.task_metadata_store import InMemoryTaskMetadataStore, PendingTaskMetadata
from app.core.orchestrator.task_result_store import InMemoryTaskResultStore
from app.core.orchestrator.pg_task_runtime import PgResultTaskRuntime

pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.contract]

# La conftest sostituisce nothing_left per i test dell'endpoint: qui la vera.
REAL_NOTHING_LEFT = task_cleanup.nothing_left


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

    def __init__(self, statuses: list[str], *, failing: tuple[str, ...] = ()):
        self.events: list[str] = []
        self._statuses = list(statuses)
        self._failing = set(failing)

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
            if name in self._failing:
                raise RuntimeError(f"{name} giu'")
            return True
        return _do


def _patched(rec: _Recorder):
    svc = task_service_module.task_service
    return [
        patch.object(svc, "cancel_task", side_effect=rec.cancel),
        patch.object(svc, "get_task_status", side_effect=rec.status),
        patch.object(svc, "forget_task_result", side_effect=rec.step("forget")),
        patch.object(svc.metadata_store, "delete", side_effect=rec.step("forget_metadata")),
        patch("app.core.results.cleanup.delete_result", side_effect=rec.step("delete_result")),
        patch("app.core.orchestrator.log_cleanup.delete_task_log_runs", side_effect=rec.step("delete_logs")),
        patch.object(task_cleanup, "request_cleanup", side_effect=lambda tid: rec.events.append("request")),
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


DATA_STEPS = ("delete_result", "delete_logs")


def test_purge_refuses_a_task_that_is_not_terminal():
    for status in ("pending", "started", "progress", "retry", None):
        with pytest.raises(RuntimeError):
            task_cleanup.purge_terminal_task("t", _info(status))


def test_terminal_task_is_cleaned_at_once_record_and_metadata_last():
    rec = _Recorder(["success"])
    out = _run(rec, lambda: task_cleanup.cleanup_task("t", _info("success", result={"result_id": "res-1"})))
    assert out.deferred is False and out.failed_steps == ()
    assert "cancel" not in rec.events and "request" not in rec.events
    # H1: metadati e record per ultimi (dopo, il task e' «niente»).
    assert rec.events[-2:] == ["forget_metadata", "forget"]
    assert set(DATA_STEPS) <= set(rec.events[:-2])


def test_partial_cleanup_keeps_the_record_and_a_retry_finishes_it():
    """H1: prima un 207 toglieva comunque il record; la ripetizione vedeva un
    task PENDING senza payload e saltava i passi falliti."""
    rec = _Recorder(["success"], failing=("delete_logs",))
    info = _info("success", result={"result_id": "res-1"})
    out = _run(rec, lambda: task_cleanup.cleanup_task("t", info))
    assert out.failed_steps == ("delete_log_runs",)
    assert "forget" not in rec.events and "forget_metadata" not in rec.events
    # Ripetizione con lo stesso record (ancora terminale, payload intero).
    rec2 = _Recorder(["success"])
    out2 = _run(rec2, lambda: task_cleanup.cleanup_task("t", info))
    assert out2.failed_steps == ()
    assert set(DATA_STEPS) <= set(rec2.events) and rec2.events[-1] == "forget"


def test_running_task_is_annotated_before_the_status_is_read_again_and_answers_at_once():
    rec = _Recorder(["progress"])
    with patch("time.sleep", side_effect=AssertionError("nessuna attesa")):
        out = _run(rec, lambda: task_cleanup.cleanup_task("t", _info("progress")))
    assert out.deferred is True
    assert rec.events == ["request", "cancel", "status"]


def test_running_task_that_finished_meanwhile_is_cleaned_by_this_call():
    rec = _Recorder(["revoked"])
    out = _run(rec, lambda: task_cleanup.cleanup_task("t", _info("started")))
    assert out.deferred is False and out.failed_steps == ()
    assert rec.events[:3] == ["request", "cancel", "status"] and rec.events[-1] == "forget"


@pytest.mark.parametrize("requested, status, expect_purge", [
    (False, "success", False),
    (True, "progress", False),
    (True, "revoked", True),
    (True, "failure", True),
])
def test_whoever_closes_the_task_does_the_requested_cleanup(monkeypatch, requested, status, expect_purge):
    from app.shared.kv import InMemoryKeyValueStore

    monkeypatch.setattr(task_cleanup, "_requests", InMemoryKeyValueStore())
    if requested:
        task_cleanup._requests.set("plenora:orchestrator:task_cleanup:t", "1")
    purged: list = []
    svc = task_service_module.task_service
    with patch.object(svc, "get_task_status", return_value=_info(status)), \
         patch.object(task_cleanup, "purge_terminal_task", side_effect=lambda tid, info: purged.append(tid) or []):
        out = task_cleanup.finish_requested_cleanup("t")
    assert (purged == ["t"]) is expect_purge
    assert (out == []) is expect_purge
    # Fatta -> richiesta tolta; non fatta -> resta.
    assert task_cleanup.cleanup_requested("t") is (requested and not expect_purge)


def test_a_failed_requested_cleanup_stays_requested(monkeypatch):
    from app.shared.kv import InMemoryKeyValueStore

    monkeypatch.setattr(task_cleanup, "_requests", InMemoryKeyValueStore())
    task_cleanup.request_cleanup("t")
    svc = task_service_module.task_service
    with patch.object(svc, "get_task_status", return_value=_info("success")), \
         patch.object(task_cleanup, "purge_terminal_task", return_value=["delete_log_runs"]):
        assert task_cleanup.finish_requested_cleanup("t") == ["delete_log_runs"]
    assert task_cleanup.cleanup_requested("t") is True


def test_nothing_left_is_no_record_and_no_metadata():
    """H2: il marcatore «gia' pulito» non e' lo stato PENDING."""
    results = InMemoryTaskResultStore()
    metadata = InMemoryTaskMetadataStore()
    service = SimpleNamespace(runtime=PgResultTaskRuntime(store=results), metadata_store=metadata)
    with patch.object(task_service_module, "task_service", service):
        assert REAL_NOTHING_LEFT("t") is True
        # In coda: nessun record ancora, ma i metadati ci sono.
        metadata.put(PendingTaskMetadata(task_id="t", project_id="p1", task_type="execute"))
        assert REAL_NOTHING_LEFT("t") is False
        metadata.delete("t")
        results.put("t", state="SUCCESS", payload={"result_id": "r"})
        assert REAL_NOTHING_LEFT("t") is False
        results.forget("t")
        assert REAL_NOTHING_LEFT("t") is True


def test_endpoint_answers_204_at_once_when_nothing_is_left(client, monkeypatch):
    rec = _Recorder(["pending"])
    monkeypatch.setattr(task_cleanup, "nothing_left", lambda task_id: True)
    response = _run(rec, lambda: client.delete("/api/tasks/t-gone/full-cleanup"))
    assert response.status_code == 204
    # Ne' annullamento, ne' lettura dello stato, ne' richiesta annotata.
    assert rec.events == []


def test_endpoint_answers_202_for_a_running_task_without_purging(client):
    rec = _Recorder(["progress"])
    with patch("app.core.projects.service.ProjectService.get_project_metadata", return_value={"id": "p1", "group_id": "grp-1"}):
        response = _run(rec, lambda: client.delete("/api/tasks/t-run/full-cleanup"))
    assert response.status_code == 202
    assert response.json() == {"status": "deferred", "task_id": "t-run"}
    assert "cancel" in rec.events and "request" in rec.events
    assert not set(DATA_STEPS) & set(rec.events)


def test_endpoint_cleans_a_finished_task_with_204(client, tmp_path):
    rec = _Recorder(["success"])
    with patch("app.core.projects.service.ProjectService.get_project_metadata", return_value={"id": "p1", "group_id": "grp-1"}), \
         patch("app.core.orchestrator.task_cleanup.settings", new=SimpleNamespace(RESULTS_DIR=tmp_path)):
        response = _run(rec, lambda: client.delete("/api/tasks/t-done/full-cleanup"))
    assert response.status_code == 204
    assert "cancel" not in rec.events and set(DATA_STEPS) <= set(rec.events)


def test_worker_does_the_requested_cleanup_after_writing_the_terminal_state():
    import workers.dramatiq_app as app_mod

    events: list[str] = []
    message = SimpleNamespace(message_id="t-1", actor_name="pipeline.execute", kwargs={}, options={})
    with patch("app.core.orchestrator.task_progress.finalize_from_celery", side_effect=lambda *a, **k: events.append("final")), \
         patch("app.core.orchestrator.task_cleanup.finish_requested_cleanup", side_effect=lambda tid: events.append(f"cleanup:{tid}") or None):
        app_mod._TaskResultMiddleware().after_process_message(MagicMock(), message, result={"success": True})
    assert events == ["final", "cleanup:t-1"]


def test_beat_does_the_requested_cleanup_of_a_dead_worker_task(monkeypatch):
    import workers.beat_scheduler as beat

    done: list[str] = []
    monkeypatch.setattr(beat, "recover_stale_task_claims", lambda **k: [SimpleNamespace(task_id="t-dead")])
    monkeypatch.setattr(beat, "cleanup_runtime_leases", lambda **k: (0, 0))
    with patch("app.core.orchestrator.task_cleanup.finish_requested_cleanup", side_effect=done.append):
        beat.PlenoraBeatScheduler._recover_runtime_leases(SimpleNamespace())
    assert done == ["t-dead"]


def test_no_cleanup_queue_or_sweep_is_left():
    """Il macchinario tolto non torna: niente coda, sweep, attese."""
    from app.config import Settings
    from app.core.scheduler.beat_schedule_config import beat_schedule_config
    from workers.dramatiq_app import TASK_QUEUE_ROUTES

    assert not any("task_cleanup" in str(e.get("task")) for e in beat_schedule_config().values())
    assert not any("task_cleanup" in name for name in TASK_QUEUE_ROUTES)
    assert not any(k.startswith("TASK_CLEANUP_") for k in Settings.DEFAULTS)
    import inspect
    src = inspect.getsource(task_cleanup)
    assert "task_cleanup_requests" not in src and "sleep" not in src


def test_the_router_has_no_purge_steps_of_its_own():
    """La pulizia e' solo in task_cleanup (una regola, un posto)."""
    import inspect

    from app.core.orchestrator import router

    src = inspect.getsource(router.cleanup_task_completely)
    assert "task_cleanup.cleanup_task(" in src and "task_cleanup.nothing_left(" in src
    for step in ("delete_result(", "delete_task_log_runs(", "forget_task_result(", "delete_task_outputs("):
        assert step not in src
