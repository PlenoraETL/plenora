"""
Contract test F3-B1 (2026-04-24) — POST create endpoints ritornano 201.

Blinda convenzione REST (RFC 9110): POST che crea una nuova risorsa
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shared.contracts.auth import CurrentUser
from app.core.auth.auth import require_admin


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


@pytest.fixture
def admin_user() -> CurrentUser:
    return CurrentUser(
        user_id="u-admin",
        username="admin",
        group_id="grp-1",
        role="admin",
    )


def test_post_connections_returns_201_on_create(admin_user, monkeypatch):
    """F3-B1: POST /api/connections → 201 Created (non 200 OK)."""
    from app.core.connections import router as connections_router_module
    from app.core.connections.router import router as connections_router
    from app.core.connections.models import DatabaseConnection

    from datetime import datetime, timezone
    # Mock del service per restituire una DatabaseConnection valida
    mock_service = MagicMock()
    mock_service.create_connection.return_value = DatabaseConnection(
        id="conn-new-1",
        name="demo",
        description="",
        type="postgres",
        host="localhost",
        port=5432,
        database="db",
        db_schema="public",
        username="u",
        password="p",
        group_id="grp-1",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        connections_router_module, "_connection_service", mock_service,
    )

    app = FastAPI()
    app.dependency_overrides[require_admin] = lambda: admin_user
    app.include_router(connections_router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/connections",
        json={
            "name": "demo",
            "type": "postgres",
            "host": "localhost",
            "port": 5432,
            "database": "db",
            "db_schema": "public",
            "username": "u",
            "password": "p",
        },
    )

    assert response.status_code == 201, (
        f"POST /api/connections DEVE ritornare 201 Created su create, "
        f"ottenuto {response.status_code}: {response.text[:200]}"
    )
    # Body invariato (Pydantic model serializzato)
    body = response.json()
    assert body["id"] == "conn-new-1"
    assert body["name"] == "demo"


def test_post_api_connections_returns_201_on_create(admin_user, monkeypatch):
    """F3-B1: POST /api/api-connections → 201 Created."""
    from app.core.arricchisci import router as arr_router_module
    from app.core.arricchisci.router import api_connections_router

    mock_api_service = MagicMock()
    mock_api_service.create_api_connection.return_value = {
        "id": "api-new-1",
        "name": "demo-api",
        "connection_type": "rest_file",
    }
    monkeypatch.setattr(
        arr_router_module, "_api_connection_service", mock_api_service,
    )

    app = FastAPI()
    app.dependency_overrides[require_admin] = lambda: admin_user
    app.include_router(api_connections_router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/api-connections",
        json={
            "name": "demo-api",
            "connection_type": "rest_file",
            "url": "https://example.test/process",
        },
    )

    assert response.status_code == 201, (
        f"POST /api/api-connections DEVE ritornare 201 Created su create, "
        f"ottenuto {response.status_code}: {response.text[:200]}"
    )
    body = response.json()
    assert body["id"] == "api-new-1"


def test_post_schedules_declares_201_on_route():
    """F3-B1: POST /api/schedules dichiara status_code=201 sul decoratore.

    Introspection del router: ScheduleConfig ha schema Pydantic nested
    (pipeline: SchedulePipeline, schedule: ScheduleFrequency) che rende
    fragile costruire una fixture completa via TestClient. Il contract
    da blindare e' che il decoratore `@router.post("", status_code=201)`
    non regredisca a 200 in un refactor.
    """
    from app.core.scheduler.router import router as sched_router

    # Il router scheduler e' mount-ato con prefix="/schedules";
    # FastAPI expande `@router.post("")` a path "/schedules".
    create_routes = [
        r for r in sched_router.routes
        if getattr(r, "path", None) == "/schedules"
        and "POST" in getattr(r, "methods", set())
    ]
    assert len(create_routes) == 1, (
        f"mi aspetto esattamente 1 route POST '/schedules' (create), "
        f"trovato: {[(r.path, r.methods) for r in sched_router.routes if 'POST' in getattr(r, 'methods', set())]!r}"
    )
    create_route = create_routes[0]

    # FastAPI espone status_code dichiarato nel decoratore come
    # `APIRoute.status_code`; se None → default FastAPI (200).
    assert create_route.status_code == 201, (
        f"POST /schedules DEVE dichiarare status_code=201 nel decoratore; "
        f"ottenuto {create_route.status_code!r} (None = default 200)"
    )
