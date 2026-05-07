"""
Contract test F3 (2026-04-24) - POST create endpoints ritornano 201.

Blinda convenzione REST (RFC 9110): POST che crea una nuova risorsa.
"""

from __future__ import annotations

from importlib import import_module
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.auth import require_admin
from app.shared.contracts.auth import CurrentUser
from tests2.shared._route_status_helpers import _assert_post_route_status_201


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


POST_201_ROUTE_CASES = [
    pytest.param(
        "app.core.scheduler.router",
        "router",
        "/schedules",
        "POST /api/schedules",
        id="schedules",
    ),
    pytest.param(
        "app.core.projects.router",
        "router",
        "/projects",
        "POST /api/projects",
        id="projects",
    ),
    pytest.param(
        "app.core.eseguibili.router",
        "router",
        "/eseguibili",
        "POST /api/eseguibili",
        id="eseguibili",
    ),
    pytest.param(
        "app.core.features.routes",
        "router",
        "/features/connections",
        "POST /api/features/connections",
        id="features-connections",
    ),
    pytest.param(
        "app.core.files.router",
        "router",
        "/files/upload",
        "POST /api/files/upload",
        id="files-upload",
    ),
    pytest.param(
        "app.core.files.router",
        "router",
        "/files/upload-multi-layer",
        "POST /api/files/upload-multi-layer",
        id="files-upload-multi-layer",
    ),
]


@pytest.fixture
def admin_user() -> CurrentUser:
    return CurrentUser(
        user_id="u-admin",
        username="admin",
        group_id="grp-1",
        role="admin",
    )


def test_post_connections_returns_201_on_create(
    admin_user: CurrentUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F3-B1: POST /api/connections -> 201 Created (non 200 OK)."""
    from datetime import datetime, timezone

    from app.core.connections import router as connections_router_module
    from app.core.connections.models import DatabaseConnection
    from app.core.connections.router import router as connections_router

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
        connections_router_module,
        "_connection_service",
        mock_service,
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
    body = response.json()
    assert body["id"] == "conn-new-1"
    assert body["name"] == "demo"


def test_post_api_connections_returns_201_on_create(
    admin_user: CurrentUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F3-B1: POST /api/api-connections -> 201 Created."""
    from app.core.arricchisci import router as arr_router_module
    from app.core.arricchisci.router import api_connections_router

    mock_api_service = MagicMock()
    mock_api_service.create_api_connection.return_value = {
        "id": "api-new-1",
        "name": "demo-api",
        "connection_type": "rest_file",
    }
    monkeypatch.setattr(
        arr_router_module,
        "_api_connection_service",
        mock_api_service,
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


@pytest.mark.parametrize(
    ("router_module", "router_name", "path", "label"),
    POST_201_ROUTE_CASES,
)
def test_post_create_declares_201_on_route(
    router_module: str,
    router_name: str,
    path: str,
    label: str,
) -> None:
    """F3: POST create-route contract dichiara status_code=201."""
    router = getattr(import_module(router_module), router_name)

    _assert_post_route_status_201(router, path, label)
