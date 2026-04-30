"""Cross-tenant leak regression — cascade 409 detail (PR-δ).

Bug 2026-04-30 (Auth/RBAC fase 2 hardening):

  3 endpoint DELETE che rifiutano con 409 cascade quando una risorsa
  e' usata in canvas di progetti, esponevano `dependencies[].name` di
  TUTTI i gruppi nel detail dell'errore. Caso di leak reale: risorsa
  shared con altri gruppi via allowed_groups, admin del gruppo
  proprietario tenta delete -> errore 409 espone nomi di progetti del
  gruppo destinatario dello share.

  Endpoint coinvolti:
    - DELETE /api/datasources/{ds_id}
    - DELETE /api/connections/{connection_id}
    - DELETE /api/api-connections/{api_id}

  Fix PR-δ:
    - dependencies filtrate per `group_id == current_user.group_id` (o
      tutte se superadmin)
    - aggiunto `hidden_count` opzionale nel detail per segnalare i
      progetti bloccanti non visibili
    - shape backward-compatible: hidden_count default 0; FE legge il
      nuovo campo via formatter centralizzato (api/errors.js)

Questo file blinda il contratto "no leak nomi cross-tenant", parametrico
sui 3 endpoint per uniformita'.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.auth import CurrentUser, require_admin, require_not_esecutore


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.regression]


# =============================================================================
# Helpers
# =============================================================================


def _admin_grp1() -> CurrentUser:
    return CurrentUser(
        user_id="u-admin-1", username="admin1", group_id="grp-1", role="admin"
    )


def _superadmin() -> CurrentUser:
    return CurrentUser(
        user_id="u-super", username="super", group_id="grp-super", role="superadmin"
    )


def _two_groups_rows():
    """Mock rows: 1 progetto in grp-1 (visibile ad admin1) + 1 progetto
    in grp-2 (cross-tenant, deve essere nascosto)."""
    return [
        {"id": "proj-own",   "name": "Mio Progetto",          "project_type": "manipola",    "group_id": "grp-1"},
        {"id": "proj-other", "name": "Progetto Altro Gruppo", "project_type": "arricchisci", "group_id": "grp-2"},
    ]


def _assert_no_leak(detail, *, leaked_name: str, leaked_id: str):
    """Asserzione generica: il nome/id del progetto cross-tenant non
    deve apparire ne' nelle dependencies ne' nel message."""
    deps = detail.get("dependencies", [])
    names = [d.get("name") for d in deps]
    ids = [d.get("id") for d in deps]
    assert leaked_name not in names, (
        f"LEAK: nome cross-tenant '{leaked_name}' presente in "
        f"dependencies={names}"
    )
    assert leaked_id not in ids, (
        f"LEAK: id cross-tenant '{leaked_id}' presente in dependencies={ids}"
    )
    msg = detail.get("message", "")
    assert leaked_name not in msg, (
        f"LEAK: nome cross-tenant '{leaked_name}' presente in "
        f"message='{msg}'"
    )


# =============================================================================
# datasources DELETE
# =============================================================================


@pytest.fixture
def datasources_client(monkeypatch):
    from app.core.datasources import router as ds_router_module
    from app.core.datasources.router import router as ds_router

    mock_svc = MagicMock()
    mock_svc.get_datasource.return_value = {
        "id": "ds-1", "group_id": "grp-1", "created_by": "u-admin-1",
    }
    monkeypatch.setattr(ds_router_module, "_svc", lambda: mock_svc)
    monkeypatch.setattr(ds_router_module, "assert_can_modify", lambda *a, **kw: None)
    monkeypatch.setattr(
        ds_router_module, "_find_projects_using_datasource",
        lambda ds_id: _two_groups_rows(),
    )

    def _build(user: CurrentUser) -> TestClient:
        app = FastAPI()
        app.dependency_overrides[require_admin] = lambda: user
        app.dependency_overrides[require_not_esecutore] = lambda: user
        app.include_router(ds_router, prefix="/api")
        return TestClient(app, raise_server_exceptions=False)

    return _build


def test_datasources_delete_cascade_filters_cross_tenant_for_admin(datasources_client):
    client = datasources_client(_admin_grp1())
    resp = client.delete("/api/datasources/ds-1")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "datasource_in_use"
    # Dependencies filtrate: solo progetto del gruppo dell'admin
    assert len(detail["dependencies"]) == 1
    assert detail["dependencies"][0]["id"] == "proj-own"
    assert detail["hidden_count"] == 1
    _assert_no_leak(
        detail, leaked_name="Progetto Altro Gruppo", leaked_id="proj-other"
    )


def test_datasources_delete_cascade_superadmin_sees_all(datasources_client):
    client = datasources_client(_superadmin())
    resp = client.delete("/api/datasources/ds-1")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    # Superadmin: tutte e 2 visibili, hidden_count = 0
    assert len(detail["dependencies"]) == 2
    assert detail["hidden_count"] == 0


# =============================================================================
# connections DELETE
# =============================================================================


@pytest.fixture
def connections_client(monkeypatch):
    from app.core.connections import router as conn_router_module
    from app.core.connections.router import router as conn_router

    mock_svc = MagicMock()
    mock_svc.get_connection.return_value = {
        "id": "conn-1", "group_id": "grp-1", "created_by": "u-admin-1",
    }
    monkeypatch.setattr(
        conn_router_module, "_connection_service", mock_svc,
    )
    monkeypatch.setattr(
        conn_router_module, "assert_can_modify", lambda *a, **kw: None,
    )
    # Mock _db.fetchall che il router chiama internamente per il cascade.
    import app.db.db_service as db_service
    monkeypatch.setattr(
        db_service, "fetchall", lambda *a, **kw: _two_groups_rows(),
    )

    def _build(user: CurrentUser) -> TestClient:
        app = FastAPI()
        app.dependency_overrides[require_admin] = lambda: user
        app.dependency_overrides[require_not_esecutore] = lambda: user
        app.include_router(conn_router, prefix="/api")
        return TestClient(app, raise_server_exceptions=False)

    return _build


def test_connections_delete_cascade_filters_cross_tenant_for_admin(connections_client):
    client = connections_client(_admin_grp1())
    resp = client.delete("/api/connections/conn-1")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "connection_in_use"
    assert len(detail["dependencies"]) == 1
    assert detail["dependencies"][0]["id"] == "proj-own"
    assert detail["hidden_count"] == 1
    _assert_no_leak(
        detail, leaked_name="Progetto Altro Gruppo", leaked_id="proj-other"
    )


def test_connections_delete_cascade_superadmin_sees_all(connections_client):
    client = connections_client(_superadmin())
    resp = client.delete("/api/connections/conn-1")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert len(detail["dependencies"]) == 2
    assert detail["hidden_count"] == 0


# =============================================================================
# api-connections DELETE
# =============================================================================


@pytest.fixture
def api_connections_client(monkeypatch):
    from app.core.arricchisci import router as arric_router_module
    from app.core.arricchisci.router import api_connections_router

    # Stub ownership check (consente ai test di arrivare al cascade).
    monkeypatch.setattr(
        arric_router_module,
        "_check_api_connection_ownership",
        lambda api_id, user: None,
    )
    monkeypatch.setattr(
        arric_router_module, "_api_connection_service", MagicMock(),
    )

    # Stub project_service.list_projects + get_project_canvas per simulare
    # 2 progetti arricchisci che usano la stessa api connection in gruppi
    # diversi.
    mock_proj_svc = MagicMock()
    mock_proj_svc.list_projects.return_value = MagicMock(projects=[
        {"project_id": "proj-own",   "name": "Mio Progetto Arricchisci",      "project_type": "arricchisci", "group_id": "grp-1"},
        {"project_id": "proj-other", "name": "Progetto Altro Gruppo",        "project_type": "arricchisci", "group_id": "grp-2"},
    ])

    def _canvas_with_api_id(*a, **kw):
        return MagicMock(nodes=[
            MagicMock(
                model_dump=lambda: {"data": {"api_id": "api-1"}},
            )
        ])

    mock_proj_svc.get_project_canvas.side_effect = _canvas_with_api_id
    monkeypatch.setattr(
        arric_router_module, "_project_service", mock_proj_svc,
    )

    def _build(user: CurrentUser) -> TestClient:
        app = FastAPI()
        app.dependency_overrides[require_admin] = lambda: user
        app.dependency_overrides[require_not_esecutore] = lambda: user
        app.include_router(api_connections_router, prefix="/api")
        return TestClient(app, raise_server_exceptions=False)

    return _build


def test_api_connections_delete_cascade_filters_cross_tenant_for_admin(api_connections_client):
    client = api_connections_client(_admin_grp1())
    resp = client.delete("/api/api-connections/api-1")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "api_connection_in_use"
    assert len(detail["dependencies"]) == 1
    assert detail["dependencies"][0]["id"] == "proj-own"
    assert detail["hidden_count"] == 1
    _assert_no_leak(
        detail, leaked_name="Progetto Altro Gruppo", leaked_id="proj-other"
    )


def test_api_connections_delete_cascade_superadmin_sees_all(api_connections_client):
    client = api_connections_client(_superadmin())
    resp = client.delete("/api/api-connections/api-1")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert len(detail["dependencies"]) == 2
    assert detail["hidden_count"] == 0
