"""Contract test C8 — health/readiness + request correlation.

Copre i pezzi che finora erano meno blindati:
1. `/health` e `/ready` ritornano 200/503 in base ai check dipendenze.
2. `RequestIdMiddleware` propaga/echoa `X-Request-ID` e ne genera uno se manca.
3. `JWTMiddleware` resetta `user_id` tra richieste consecutive.
4. Celery signal hooks propagano e puliscono il `request_id`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


@pytest.fixture
def main_client():
    """TestClient sull'app reale con check health/readiness monkeypatchati."""
    import app.main as main_mod

    with (
        patch.object(main_mod, "_check_postgres", return_value=(True, "ok")),
        patch.object(main_mod, "_check_redis", return_value=(True, "ok")),
        patch.object(main_mod, "_check_pool_headroom", return_value=(True, "1/12 in uso, 11 free")),
        patch.object(main_mod, "_check_migrations_aligned", return_value=(True, "aligned")),
    ):
        yield TestClient(main_mod.app)


def _build_middleware_app():
    """Mini-app isolata per testare middleware senza tutto il runtime."""
    from app.core.auth.auth_middleware import JWTMiddleware
    from app.core.auth.request_id_middleware import RequestIdMiddleware
    from app.utils.logger import get_request_id, get_user_id

    app = FastAPI()
    app.add_middleware(JWTMiddleware)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/ctx")
    async def ctx(request: Request):
        return JSONResponse(
            {
                "request_id_ctx": get_request_id(),
                "request_id_state": getattr(request.state, "request_id", ""),
                "user_id_ctx": get_user_id(),
                "user_id_state": getattr(request.state, "user_id", ""),
            }
        )

    return app


def test_health_returns_200_when_postgres_and_redis_are_ok(main_client):
    response = main_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["postgres"]["ok"] is True
    assert body["checks"]["redis"]["ok"] is True


def test_health_returns_503_when_redis_fails():
    import app.main as main_mod

    with (
        patch.object(main_mod, "_check_postgres", return_value=(True, "ok")),
        patch.object(main_mod, "_check_redis", return_value=(False, "ConnectionError: boom")),
    ):
        client = TestClient(main_mod.app)
        response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["checks"]["redis"]["ok"] is False


def test_ready_returns_503_when_pool_or_migrations_are_not_ready():
    import app.main as main_mod

    with (
        patch.object(main_mod, "_check_postgres", return_value=(True, "ok")),
        patch.object(main_mod, "_check_redis", return_value=(True, "ok")),
        patch.object(main_mod, "_check_pool_headroom", return_value=(False, "pool saturo")),
        patch.object(main_mod, "_check_migrations_aligned", return_value=(False, "2 pending")),
    ):
        client = TestClient(main_mod.app)
        response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["pool_headroom"]["ok"] is False
    assert body["checks"]["migrations"]["ok"] is False


def test_request_id_middleware_echoes_incoming_header():
    app = _build_middleware_app()
    client = TestClient(app)

    response = client.get("/ctx", headers={"X-Request-ID": "req-123"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-123"
    body = response.json()
    assert body["request_id_ctx"] == "req-123"
    assert body["request_id_state"] == "req-123"


def test_request_id_middleware_generates_header_when_missing():
    app = _build_middleware_app()
    client = TestClient(app)

    response = client.get("/ctx")

    assert response.status_code == 200
    req_id = response.headers["X-Request-ID"]
    assert isinstance(req_id, str) and len(req_id) >= 8
    body = response.json()
    assert body["request_id_ctx"] == req_id
    assert body["request_id_state"] == req_id


def test_jwt_middleware_clears_user_id_between_authenticated_and_anonymous_requests(monkeypatch):
    from app.core.auth import auth_middleware as auth_mw

    app = _build_middleware_app()
    client = TestClient(app)

    monkeypatch.setattr(
        auth_mw,
        "decode_token",
        lambda token: {
            "group_id": "grp-1",
            "sub": "user-1",
            "username": "marco",
            "role": "admin",
        } if token == "good-token" else None,
    )

    first = client.get("/ctx", headers={"Authorization": "Bearer good-token"})
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["user_id_ctx"] == "user-1"
    assert first_body["user_id_state"] == "user-1"

    second = client.get("/ctx")
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["user_id_ctx"] == ""
    assert second_body["user_id_state"] == ""


def test_celery_signals_propagate_request_id_and_clear_after_task():
    from app.utils.logger import get_request_id, set_request_id
    from workers.celery_app import (
        _clear_request_id_after_task,
        _propagate_request_id_on_publish,
        _set_request_id_from_task,
    )

    set_request_id("rid-123")
    headers = {}
    _propagate_request_id_on_publish(headers=headers)
    assert headers["X-Request-ID"] == "rid-123"

    set_request_id("")
    fake_task = SimpleNamespace(request=SimpleNamespace(headers=headers))
    _set_request_id_from_task(task_id="task-1", task=fake_task)
    assert get_request_id() == "rid-123"

    _clear_request_id_after_task()
    assert get_request_id() == ""


def test_celery_signal_falls_back_to_task_id_when_header_missing():
    from app.utils.logger import get_request_id, set_request_id
    from workers.celery_app import _clear_request_id_after_task, _set_request_id_from_task

    set_request_id("")
    fake_task = SimpleNamespace(request=SimpleNamespace(headers={}))
    _set_request_id_from_task(task_id="task-fallback-1", task=fake_task)
    assert get_request_id() == "task-fallback-1"

    _clear_request_id_after_task()
    assert get_request_id() == ""
