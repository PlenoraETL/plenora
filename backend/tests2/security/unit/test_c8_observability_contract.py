"""
Contract test C8 — health/readiness + request correlation.

Copre i pezzi che finora erano meno blindati:
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
    """TestClient sull'app reale con check health/readiness monkeypatchati.

    BE-GO-READY-BOOTSTRAP-ENGINE-009: i 4 helper sono stati estratti
    in `app/observability/health.py`; il monkeypatch attacca al
    nuovo namespace public (`check_*`). Le route in `main.py`
    delegano via `gather_liveness` / `gather_readiness`, che a loro
    volta chiamano i `check_*` patchati.

    BE-GO-READY-BOOTSTRAP-ENGINE-011: aggiunto `check_object_storage`
    al monkeypatch — quinto check di `gather_readiness`. Default
    happy path = `(True, "ok")` (capability ON + list_objects ok).

    BE-GO-READY-STORAGE-ENGINE-015: aggiunto
    `check_reconciliation_backlog` come 6° check. Default happy
    path = `(True, "pending=0 lost=0")` (tabella vuota o sotto
    threshold).
    """
    import app.main as main_mod
    import app.observability.health as health_mod

    with (
        patch.object(health_mod, "check_postgres", return_value=(True, "ok")),
        patch.object(health_mod, "check_broker", return_value=(True, "ok")),
        patch.object(health_mod, "check_pool_headroom", return_value=(True, "1/12 in uso, 11 free")),
        patch.object(health_mod, "check_migrations_aligned", return_value=(True, "aligned")),
        patch.object(health_mod, "check_object_storage", return_value=(True, "ok")),
        patch.object(
            health_mod, "check_reconciliation_backlog",
            return_value=(True, "pending=0 lost=0"),
        ),
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
        # BE-GO-READY-SECURITY-ENGINE-002-AUTH-CLEANUP-RESIDUALS:
        # `request.state.user_id` e' stato rimosso da `JWTMiddleware`
        # (era dead write — i router consumano `Depends(get_current_user)`).
        # `request.state.request_id` resta legittimo
        # (RequestIdMiddleware lo setta + e' usato da consumer reali).
        return JSONResponse(
            {
                "request_id_ctx": get_request_id(),
                "request_id_state": getattr(request.state, "request_id", ""),
                "user_id_ctx": get_user_id(),
            }
        )

    return app


def test_health_returns_200_when_process_responds(main_client):
    response = main_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {
        "process": {"ok": True, "detail": "alive"},
    }


def test_health_stays_200_when_broker_fails():
    import app.main as main_mod
    import app.observability.health as health_mod

    with (
        patch.object(
            health_mod,
            "check_broker",
            return_value=(False, "ConnectionError: boom"),
        ),
    ):
        client = TestClient(main_mod.app)
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert list(body["checks"].keys()) == ["process"]


def test_ready_returns_503_when_broker_fails():
    import app.main as main_mod
    import app.observability.health as health_mod

    with (
        patch.object(health_mod, "check_postgres", return_value=(True, "ok")),
        patch.object(
            health_mod,
            "check_broker",
            return_value=(False, "ConnectionError: boom"),
        ),
        patch.object(health_mod, "check_pool_headroom", return_value=(True, "ok")),
        patch.object(
            health_mod,
            "check_migrations_aligned",
            return_value=(True, "aligned"),
        ),
        patch.object(health_mod, "check_object_storage", return_value=(True, "ok")),
        patch.object(
            health_mod,
            "check_reconciliation_backlog",
            return_value=(True, "pending=0 lost=0"),
        ),
        patch.object(
            health_mod,
            "check_beat_heartbeat",
            return_value=(True, "skipped: disabled (threshold=0)"),
        ),
        patch.object(
            health_mod,
            "check_bootstrap_complete",
            return_value=(True, "ok"),
        ),
    ):
        client = TestClient(main_mod.app)
        response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["broker"]["ok"] is False


def test_ready_returns_503_when_pool_or_migrations_are_not_ready():
    import app.main as main_mod
    import app.observability.health as health_mod

    with (
        patch.object(health_mod, "check_postgres", return_value=(True, "ok")),
        patch.object(health_mod, "check_broker", return_value=(True, "ok")),
        patch.object(health_mod, "check_pool_headroom", return_value=(False, "pool saturo")),
        patch.object(health_mod, "check_migrations_aligned", return_value=(False, "2 pending")),
        patch.object(health_mod, "check_object_storage", return_value=(True, "ok")),
        patch.object(
            health_mod, "check_reconciliation_backlog",
            return_value=(True, "pending=0 lost=0"),
        ),
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

    second = client.get("/ctx")
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["user_id_ctx"] == ""


def test_dramatiq_request_id_middleware_propagates_and_clears():
    # Plenora 2.0 Fase 5: la propagazione del correlation id attraverso il
    # broker e' su middleware Dramatiq (rimpiazzo dei signal Celery
    # before_task_publish/task_prerun/task_postrun). Dramatiq non ha header
    # nativi: il request_id viaggia nelle message.options.
    from app.utils.logger import get_request_id, set_request_id
    from workers.dramatiq_app import _REQUEST_ID_OPTION, _RequestIdMiddleware

    mw = _RequestIdMiddleware()

    # Lato dispatch (es. API): copia il request_id locale nelle options.
    set_request_id("rid-123")
    message = SimpleNamespace(options={}, message_id="msg-1")
    mw.before_enqueue(None, message, None)
    assert message.options[_REQUEST_ID_OPTION] == "rid-123"

    # Lato worker: ripristina il request_id dal messaggio.
    set_request_id("")
    mw.before_process_message(None, message)
    assert get_request_id() == "rid-123"

    # Cleanup dopo il task: niente leak tra task consecutivi.
    mw.after_process_message(None, message)
    assert get_request_id() == ""


def test_dramatiq_request_id_middleware_falls_back_to_message_id():
    # Senza request_id nel messaggio (es. dispatch dal beat) il worker
    # ricade sul message_id, come task_prerun ricadeva sul task_id.
    from app.utils.logger import get_request_id, set_request_id
    from workers.dramatiq_app import _RequestIdMiddleware

    mw = _RequestIdMiddleware()
    set_request_id("")
    message = SimpleNamespace(options={}, message_id="msg-fallback-1")
    mw.before_process_message(None, message)
    assert get_request_id() == "msg-fallback-1"

    mw.after_process_message(None, message)
    assert get_request_id() == ""


def test_dramatiq_request_id_not_injected_when_absent():
    # Nessun request_id locale: niente option iniettata sul messaggio.
    from app.utils.logger import set_request_id
    from workers.dramatiq_app import _REQUEST_ID_OPTION, _RequestIdMiddleware

    mw = _RequestIdMiddleware()
    set_request_id("")
    message = SimpleNamespace(options={}, message_id="msg-2")
    mw.before_enqueue(None, message, None)
    assert _REQUEST_ID_OPTION not in message.options
