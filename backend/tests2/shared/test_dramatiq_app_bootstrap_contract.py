"""
Contratto bootstrap Dramatiq (Plenora 2.0, Fase 1 — strangler broker).

Finche' Celery e Dramatiq convivono, la topologia di routing dei due
stack deve restare identica: stesso nome task -> stessa coda. L'actor
pilota cleanroom.ping e' registrato con il nome Celery storico.
"""

from __future__ import annotations

import pytest

pytest.importorskip("dramatiq", reason="dramatiq non installato (Fase 1)")

pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


def test_queue_routes_match_celery_task_routes():
    from workers.celery_app import celery_app
    from workers.dramatiq_app import TASK_QUEUE_ROUTES

    celery_routes = {
        name: route["queue"]
        for name, route in (celery_app.conf.task_routes or {}).items()
    }
    assert TASK_QUEUE_ROUTES == celery_routes, (
        "La tabella di routing Dramatiq deve restare identica a "
        "celery_app.task_routes finche' i due stack convivono"
    )


def test_default_queue_matches_celery_default():
    from app.config import settings
    from workers.dramatiq_app import queue_for

    assert queue_for("nome-non-mappato") == settings.CELERY_QUEUE_DEFAULT


def test_cleanroom_ping_actor_keeps_celery_name_and_default_queue():
    from app.config import settings
    from workers.dramatiq_app import cleanroom_ping

    assert cleanroom_ping.actor_name == "cleanroom.ping"
    assert cleanroom_ping.queue_name == settings.CELERY_QUEUE_DEFAULT
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
    assert cleanup_temp_files.queue_name == settings.CELERY_QUEUE_CONTROL_PLANE

    # Corpo condiviso: l'actor delega alla stessa funzione del task Celery
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
        settings.CELERY_QUEUE_CONTROL_PLANE
    )

    # Corpo condiviso: l'actor delega alla stessa funzione del task Celery
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
    # Chiusura Fase 1: 10/10 task Celery hanno un actor Dramatiq omonimo.
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


import pytest as _pytest


@_pytest.mark.parametrize(
    "attr, actor_name, queue_attr",
    [
        ("carica_execute", "carica.execute", "CELERY_QUEUE_DATA_PLANE"),
        ("pipeline_execute", "pipeline.execute", "CELERY_QUEUE_DATA_PLANE"),
        ("pipeline_preview", "pipeline.preview", "CELERY_QUEUE_DATA_PLANE"),
        ("execute_enrichment", "tasks.execute_enrichment", "CELERY_QUEUE_DATA_PLANE"),
        (
            "schedule_pipeline_task",
            "workers.tasks.schedule_task.schedule_pipeline_task",
            "CELERY_QUEUE_DATA_PLANE",
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
    # Corpo condiviso: l'actor delega al corpo del task Celery via shim.
    assert "_invoke_celery_task_body" in inspect.getsource(actor.fn)


def test_beat_heartbeat_actor_name_and_scheduler_queue():
    from app.config import settings
    from workers.dramatiq_app import beat_heartbeat

    assert beat_heartbeat.actor_name == (
        "workers.tasks.schedule_task.beat_heartbeat"
    )
    assert beat_heartbeat.queue_name == settings.CELERY_QUEUE_SCHEDULER


def test_broker_has_task_result_finalize_middleware():
    from workers.dramatiq_app import broker

    names = {type(m).__name__ for m in broker.middleware}
    assert "_TaskResultMiddleware" in names, (
        "il broker Dramatiq deve avere il middleware di finalize esito su PG"
    )
    assert "CurrentMessage" in names


def test_invoke_celery_task_body_injects_message_id_as_self_request_id(monkeypatch):
    from types import SimpleNamespace

    from workers import dramatiq_app

    monkeypatch.setattr(dramatiq_app, "current_message_id", lambda: "msg-123")

    captured = {}

    def _raw(self, **kwargs):
        captured["task_id"] = self.request.id
        captured["name"] = self.name
        captured["kwargs"] = kwargs
        return {"ok": True}

    fake_task = SimpleNamespace(__wrapped__=SimpleNamespace(__func__=_raw))

    result = dramatiq_app._invoke_celery_task_body(
        fake_task, name="carica.execute", kwargs={"project_id": "p1"}
    )

    assert result == {"ok": True}
    assert captured == {
        "task_id": "msg-123",
        "name": "carica.execute",
        "kwargs": {"project_id": "p1"},
    }


def test_real_celery_tasks_expose_wrapped_for_shim_invocation():
    # Lo shim degli actor invoca task.__wrapped__ (.__func__): qui si blinda
    # che i task Celery reali espongano davvero il seam, senza eseguirne il
    # corpo (no DB). Se Celery cambiasse il wrapping, questo test lo rileva.
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
        wrapped = task.__wrapped__
        raw = getattr(wrapped, "__func__", wrapped)
        assert callable(raw), f"{task.name}: __wrapped__ non risolvibile per lo shim"


def test_broker_url_overridable_via_env(monkeypatch):
    from workers import dramatiq_app

    monkeypatch.setenv("DRAMATIQ_BROKER_URL", "amqp://x:y@altrove:5672//")
    assert dramatiq_app.broker_url() == "amqp://x:y@altrove:5672//"
