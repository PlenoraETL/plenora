"""
Contratto bootstrap Dramatiq (Plenora 2.0, Fase 1 — strangler broker).

Finche' Celery e Dramatiq convivono, la topologia di routing dei due
stack deve restare identica: stesso nome task -> stessa coda. L'actor
pilota cleanroom.ping e' registrato con il nome Celery storico.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

pytest.importorskip("dramatiq", reason="dramatiq non installato (Fase 1)")

pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


def test_queue_routes_map_tasks_to_canonical_queues():
    # Plenora 2.0 Fase 5: rimosso celery_app; la topologia di routing
    # (data-plane / control-plane / scheduler) e' ora la sorgente di verita'
    # di workers.dramatiq_app, blindata qui esplicitamente.
    from app.config import settings
    from workers.dramatiq_app import TASK_QUEUE_ROUTES

    expected = {
        "pipeline.execute": settings.TASK_QUEUE_DATA_PLANE,
        "pipeline.preview": settings.TASK_QUEUE_DATA_PLANE,
        "carica.execute": settings.TASK_QUEUE_DATA_PLANE,
        "tasks.execute_enrichment": settings.TASK_QUEUE_DATA_PLANE,
        "workers.tasks.admin_backup_task.run_manual_backup_task": (
            settings.TASK_QUEUE_CONTROL_PLANE
        ),
        "workers.tasks.cleanup_temp_files_task.run": (
            settings.TASK_QUEUE_CONTROL_PLANE
        ),
        "workers.tasks.schedule_task.schedule_pipeline_task": (
            settings.TASK_QUEUE_DATA_PLANE
        ),
        "workers.tasks.schedule_task.beat_heartbeat": (
            settings.TASK_QUEUE_SCHEDULER
        ),
    }
    assert TASK_QUEUE_ROUTES == expected


def test_default_queue_matches_celery_default():
    from app.config import settings
    from workers.dramatiq_app import queue_for

    assert queue_for("nome-non-mappato") == settings.TASK_QUEUE_DEFAULT


def test_cleanroom_ping_actor_keeps_celery_name_and_default_queue():
    from app.config import settings
    from workers.dramatiq_app import cleanroom_ping

    assert cleanroom_ping.actor_name == "cleanroom.ping"
    assert cleanroom_ping.queue_name == settings.TASK_QUEUE_DEFAULT
    assert cleanroom_ping.fn() == "pong"


def test_cleanroom_beat_ping_actor_parity():
    from workers.dramatiq_app import cleanroom_beat_ping

    assert cleanroom_beat_ping.actor_name == "cleanroom.beat_ping"
    assert cleanroom_beat_ping.fn() == "beat_pong"


def test_cleanup_temp_files_actor_routes_to_control_plane_and_shares_body():
    from app.config import settings
    from workers.dramatiq_app import cleanup_temp_files

    assert cleanup_temp_files.actor_name == (
        "workers.tasks.cleanup_temp_files_task.run"
    )
    assert cleanup_temp_files.queue_name == settings.TASK_QUEUE_CONTROL_PLANE

    # Corpo condiviso: l'actor delega alla stessa funzione pura del task
    # (lazy import nel body — verifica per identita' del simbolo sorgente).
    import inspect

    assert "run_cleanup_temp_files" in inspect.getsource(cleanup_temp_files.fn)


def test_admin_backup_actor_routes_to_control_plane_and_shares_body():
    from app.config import settings
    from workers.dramatiq_app import run_manual_backup_task

    assert run_manual_backup_task.actor_name == (
        "workers.tasks.admin_backup_task.run_manual_backup_task"
    )
    assert run_manual_backup_task.queue_name == (
        settings.TASK_QUEUE_CONTROL_PLANE
    )

    # Corpo condiviso: l'actor delega alla stessa funzione pura del task
    # e inietta il message_id come task_id (rimpiazzo di self.request.id).
    import inspect

    src = inspect.getsource(run_manual_backup_task.fn)
    assert "run_manual_backup" in src
    assert "current_message_id" in src


def test_current_message_id_none_outside_actor():
    # Fuori da un actor in esecuzione non c'e' messaggio corrente: il
    # rimpiazzo di self.request.id ricade su None (come Celery eager).
    from workers.dramatiq_app import current_message_id

    assert current_message_id() is None


def test_all_ten_task_names_have_a_dramatiq_actor():
    # Chiusura Fase 1: 10/10 task hanno un actor Dramatiq omonimo.
    from workers import dramatiq_app

    expected_actor_names = {
        "cleanroom.ping",
        "cleanroom.beat_ping",
        "workers.tasks.cleanup_temp_files_task.run",
        "workers.tasks.admin_backup_task.run_manual_backup_task",
        "carica.execute",
        "pipeline.execute",
        "pipeline.preview",
        "tasks.execute_enrichment",
        "workers.tasks.schedule_task.schedule_pipeline_task",
        "workers.tasks.schedule_task.beat_heartbeat",
    }
    registered = {
        getattr(dramatiq_app, attr).actor_name
        for attr in dir(dramatiq_app)
        if hasattr(getattr(dramatiq_app, attr), "actor_name")
    }
    assert expected_actor_names <= registered, (
        f"Actor mancanti: {expected_actor_names - registered}"
    )


@pytest.mark.parametrize(
    "attr, actor_name, queue_attr",
    [
        ("carica_execute", "carica.execute", "TASK_QUEUE_DATA_PLANE"),
        ("pipeline_execute", "pipeline.execute", "TASK_QUEUE_DATA_PLANE"),
        ("pipeline_preview", "pipeline.preview", "TASK_QUEUE_DATA_PLANE"),
        ("execute_enrichment", "tasks.execute_enrichment", "TASK_QUEUE_DATA_PLANE"),
        (
            "schedule_pipeline_task",
            "workers.tasks.schedule_task.schedule_pipeline_task",
            "TASK_QUEUE_DATA_PLANE",
        ),
    ],
)
def test_shim_actors_keep_name_queue_and_share_body(attr, actor_name, queue_attr):
    import inspect

    from app.config import settings
    from workers import dramatiq_app

    actor = getattr(dramatiq_app, attr)
    assert actor.actor_name == actor_name
    assert actor.queue_name == getattr(settings, queue_attr)
    # Corpo condiviso: l'actor delega al corpo del task (funzione pura) via shim.
    assert "_invoke_task_body" in inspect.getsource(actor.fn)


def test_beat_heartbeat_actor_name_and_scheduler_queue():
    from app.config import settings
    from workers.dramatiq_app import beat_heartbeat

    assert beat_heartbeat.actor_name == (
        "workers.tasks.schedule_task.beat_heartbeat"
    )
    assert beat_heartbeat.queue_name == settings.TASK_QUEUE_SCHEDULER


def test_broker_has_task_result_finalize_middleware():
    from workers.dramatiq_app import broker

    names = {type(m).__name__ for m in broker.middleware}
    assert "_TaskResultMiddleware" in names, (
        "il broker Dramatiq deve avere il middleware di finalize esito su PG"
    )
    assert "CurrentMessage" in names


def test_pipeline_actor_retries_only_explicit_transient_results():
    from workers.dramatiq_app import (
        RetryablePipelineError,
        _return_or_raise_retryable,
        pipeline_execute,
    )

    assert pipeline_execute.options["max_retries"] == 2
    assert _return_or_raise_retryable({"success": False}) == {"success": False}
    with pytest.raises(RetryablePipelineError):
        _return_or_raise_retryable({"success": False, "retryable": True})


@pytest.mark.parametrize(
    "actor_attr,task_name",
    [
        ("carica_execute", "carica.execute"),
        ("pipeline_execute", "pipeline.execute"),
        ("pipeline_preview", "pipeline.preview"),
        ("execute_enrichment", "tasks.execute_enrichment"),
    ],
)
def test_heavy_actors_use_disposable_process_with_rss_cap(
    monkeypatch,
    actor_attr,
    task_name,
):
    from app.core.orchestrator.task_progress import TaskRevoked
    from app.core.resources.exceptions import MemoryLimitExceededError
    from workers import data_plane_subprocess, dramatiq_app

    captured = {}

    def _execute(**kwargs):
        captured.update(kwargs)
        return {"success": True}

    monkeypatch.setattr(dramatiq_app, "_data_plane_execution_mode", lambda: "subprocess")
    monkeypatch.setattr(dramatiq_app, "_data_plane_child_timeout_seconds", lambda: 99)
    monkeypatch.setattr(
        dramatiq_app,
        "_data_plane_child_memory_limit_bytes",
        lambda: 1_500,
    )
    monkeypatch.setattr(dramatiq_app, "current_message_id", lambda: "message-1")
    monkeypatch.setattr(data_plane_subprocess, "execute_task_in_subprocess", _execute)

    actor = getattr(dramatiq_app, actor_attr)
    assert actor.fn(project_id="project-1") == {"success": True}
    assert captured["task_name"] == task_name
    assert captured["task_id"] == "message-1"
    assert captured["timeout_seconds"] == 99
    assert captured["memory_limit_bytes"] == 1_500
    assert captured["kwargs"]["project_id"] == "project-1"
    assert TaskRevoked in actor.options["throws"]
    assert MemoryLimitExceededError in actor.options["throws"]


def test_result_middleware_exposes_retry_instead_of_terminal_failure(monkeypatch):
    from app.core.orchestrator import task_progress
    from workers.dramatiq_app import (
        RetryablePipelineError,
        _TaskResultMiddleware,
    )

    writes = []
    monkeypatch.setattr(
        task_progress,
        "finalize_task_result",
        lambda task_id, **kwargs: writes.append((task_id, kwargs)),
    )
    broker = SimpleNamespace(
        get_actor=lambda _name: SimpleNamespace(options={"max_retries": 2})
    )
    message = SimpleNamespace(
        message_id="task-1",
        actor_name="pipeline.execute",
        options={"retries": 0},
        kwargs={"run_id": "logical-run-1"},
    )

    _TaskResultMiddleware().after_process_message(
        broker,
        message,
        exception=RetryablePipelineError("transient"),
    )

    assert writes[0][0] == "task-1"
    assert writes[0][1]["state"] == "RETRY"
    assert writes[0][1]["payload"]["run_id"] == "logical-run-1"


def test_result_middleware_finalizes_the_requested_run_id(monkeypatch):
    from app.core.orchestrator import task_progress
    from workers.dramatiq_app import _TaskResultMiddleware

    calls = []
    monkeypatch.setattr(
        task_progress,
        "finalize_from_celery",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    message = SimpleNamespace(
        message_id="task-1",
        actor_name="tasks.execute_enrichment",
        options={},
        kwargs={"run_id": "logical-run-1"},
    )

    _TaskResultMiddleware().after_process_message(
        SimpleNamespace(),
        message,
        exception=RuntimeError("boom"),
    )

    assert calls[0][1]["run_id"] == "logical-run-1"


def test_invoke_task_body_injects_message_id_as_self_request_id(monkeypatch):

    from workers import dramatiq_app

    monkeypatch.setattr(dramatiq_app, "current_message_id", lambda: "msg-123")
    monkeypatch.setattr(
        dramatiq_app, "_cleanup_worker_memory_after_task", lambda name: None
    )

    captured = {}

    def _body(self, **kwargs):
        captured["task_id"] = self.request.id
        captured["name"] = self.name
        captured["kwargs"] = kwargs
        return {"ok": True}

    # Plenora 2.0 Fase 5: i corpi sono funzioni pure; lo shim le chiama dirette.
    result = dramatiq_app._invoke_task_body(
        _body, name="carica.execute", kwargs={"project_id": "p1"}
    )

    assert result == {"ok": True}
    assert captured == {
        "task_id": "msg-123",
        "name": "carica.execute",
        "kwargs": {"project_id": "p1"},
    }


def test_invoke_task_body_runs_memory_cleanup_after_success(monkeypatch):
    from workers import dramatiq_app

    cleanup_calls = []
    monkeypatch.setattr(dramatiq_app, "current_message_id", lambda: "msg-123")
    monkeypatch.setattr(
        dramatiq_app,
        "_cleanup_worker_memory_after_task",
        lambda name: cleanup_calls.append(name),
    )

    def _body(self, **kwargs):
        return {"ok": True}

    result = dramatiq_app._invoke_task_body(
        _body, name="pipeline.execute", kwargs={"project_id": "p1"}
    )

    assert result == {"ok": True}
    assert cleanup_calls == ["pipeline.execute"]


def test_invoke_task_body_runs_memory_cleanup_after_exception(monkeypatch):
    from workers import dramatiq_app

    cleanup_calls = []
    monkeypatch.setattr(dramatiq_app, "current_message_id", lambda: "msg-123")
    monkeypatch.setattr(
        dramatiq_app,
        "_cleanup_worker_memory_after_task",
        lambda name: cleanup_calls.append(name),
    )

    def _body(self, **kwargs):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        dramatiq_app._invoke_task_body(
            _body, name="pipeline.execute", kwargs={"project_id": "p1"}
        )

    assert cleanup_calls == ["pipeline.execute"]


def test_worker_memory_cleanup_helper_delegates_to_memory_guard(monkeypatch):
    from app.core.resources import memory_guard
    from workers import dramatiq_app

    captured = {}

    monkeypatch.setattr(
        dramatiq_app.settings,
        "get",
        lambda key, default=None: True,
    )

    def _cleanup_worker_memory(*, run_gc, run_malloc_trim):
        captured["run_gc"] = run_gc
        captured["run_malloc_trim"] = run_malloc_trim
        return {
            "before_rss": 2_000,
            "after_rss": 1_000,
            "collected": 3,
            "malloc_trim": True,
        }

    monkeypatch.setattr(
        memory_guard, "cleanup_worker_memory", _cleanup_worker_memory
    )

    dramatiq_app._cleanup_worker_memory_after_task("pipeline.execute")

    assert captured == {"run_gc": True, "run_malloc_trim": True}


def test_worker_memory_recycle_exits_when_rss_remains_above_cap(monkeypatch):
    from app.core.resources import memory_guard
    from workers import dramatiq_app

    def _settings_get(key, default=None):
        values = {
            "WORKER_MEMORY_RECYCLE_ENABLED": True,
            "WORKER_MEMORY_RECYCLE_RSS_BYTES": 1_500,
            "WORKER_RSS_MAX_BYTES": 1_500,
        }
        return values.get(key, default)

    monkeypatch.setattr(dramatiq_app.settings, "get", _settings_get)
    monkeypatch.setattr(memory_guard, "get_current_rss_bytes", lambda: 2_000)
    monkeypatch.setattr(dramatiq_app.logging, "shutdown", lambda: None)
    monkeypatch.setattr(dramatiq_app, "_ACTIVE_MESSAGES", 1)
    monkeypatch.setattr(dramatiq_app, "_RECYCLE_REQUESTED", False)

    def _exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(dramatiq_app.os, "_exit", _exit)

    middleware = dramatiq_app._WorkerMemoryRecycleMiddleware()
    with pytest.raises(SystemExit) as exc_info:
        middleware.after_ack(None, SimpleNamespace(actor_name="pipeline.preview"))

    assert exc_info.value.code == 0


def test_worker_memory_recycle_skips_when_rss_is_below_cap(monkeypatch):
    from app.core.resources import memory_guard
    from workers import dramatiq_app

    monkeypatch.setattr(
        dramatiq_app.settings,
        "get",
        lambda key, default=None: {
            "WORKER_MEMORY_RECYCLE_ENABLED": True,
            "WORKER_MEMORY_RECYCLE_RSS_BYTES": 1_500,
        }.get(key, default),
    )
    monkeypatch.setattr(memory_guard, "get_current_rss_bytes", lambda: 1_000)
    monkeypatch.setattr(dramatiq_app, "_ACTIVE_MESSAGES", 1)
    monkeypatch.setattr(dramatiq_app, "_RECYCLE_REQUESTED", False)

    exit_calls = []
    monkeypatch.setattr(dramatiq_app.os, "_exit", exit_calls.append)

    middleware = dramatiq_app._WorkerMemoryRecycleMiddleware()
    middleware.after_ack(None, SimpleNamespace(actor_name="pipeline.preview"))

    assert exit_calls == []


def test_worker_memory_recycle_waits_for_other_active_messages(monkeypatch):
    from app.core.resources import memory_guard
    from workers import dramatiq_app

    monkeypatch.setattr(
        dramatiq_app.settings,
        "get",
        lambda key, default=None: {
            "WORKER_MEMORY_RECYCLE_ENABLED": True,
            "WORKER_MEMORY_RECYCLE_RSS_BYTES": 1_500,
        }.get(key, default),
    )
    monkeypatch.setattr(memory_guard, "get_current_rss_bytes", lambda: 2_000)
    monkeypatch.setattr(dramatiq_app.logging, "shutdown", lambda: None)
    monkeypatch.setattr(dramatiq_app, "_ACTIVE_MESSAGES", 2)
    monkeypatch.setattr(dramatiq_app, "_RECYCLE_REQUESTED", False)

    exit_calls = []
    monkeypatch.setattr(dramatiq_app.os, "_exit", exit_calls.append)

    middleware = dramatiq_app._WorkerMemoryRecycleMiddleware()
    middleware.after_ack(None, SimpleNamespace(actor_name="pipeline.preview"))

    assert exit_calls == []
    assert dramatiq_app._ACTIVE_MESSAGES == 1
    assert dramatiq_app._RECYCLE_REQUESTED is True

    def _exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(dramatiq_app.os, "_exit", _exit)
    with pytest.raises(SystemExit):
        middleware.after_ack(None, SimpleNamespace(actor_name="pipeline.execute"))


def test_memory_recycle_middleware_runs_only_after_broker_disposition():
    from workers import dramatiq_app

    src = inspect.getsource(dramatiq_app._WorkerMemoryRecycleMiddleware)
    assert "def after_ack" in src
    assert "def after_nack" in src
    assert "def after_process_message" not in src


def test_real_task_bodies_are_plain_callables_for_shim_invocation():
    # Plenora 2.0 Fase 5: i corpi dei task sono funzioni pure (niente piu'
    # @celery_app.task / __wrapped__): lo shim le invoca direttamente. Qui si
    # blinda che restino callable accettando `self` come primo argomento.
    import inspect

    from workers.tasks.carica_task import execute_carica_task
    from workers.tasks.pipeline_task import (
        execute_pipeline_task,
        preview_pipeline_task,
    )
    from workers.tasks.enrichment_task import execute_enrichment_task
    from workers.tasks.schedule_task import schedule_pipeline_task

    for task in (
        execute_carica_task,
        execute_pipeline_task,
        preview_pipeline_task,
        execute_enrichment_task,
        schedule_pipeline_task,
    ):
        assert callable(task), f"{task.__name__}: corpo non callable per lo shim"
        assert not hasattr(task, "__wrapped__"), (
            f"{task.__name__}: non deve piu' essere un task Celery wrappato"
        )
        params = list(inspect.signature(task).parameters)
        assert params and params[0] == "self", (
            f"{task.__name__}: primo parametro deve essere il self-shim"
        )


def test_broker_url_overridable_via_env(monkeypatch):
    from workers import dramatiq_app

    monkeypatch.setenv("DRAMATIQ_BROKER_URL", "amqp://x:y@altrove:5672//")
    assert dramatiq_app.broker_url() == "amqp://x:y@altrove:5672//"
