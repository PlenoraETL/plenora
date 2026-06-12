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


def test_broker_url_overridable_via_env(monkeypatch):
    from workers import dramatiq_app

    monkeypatch.setenv("DRAMATIQ_BROKER_URL", "amqp://x:y@altrove:5672//")
    assert dramatiq_app.broker_url() == "amqp://x:y@altrove:5672//"
