"""Contract test — PATCH /api/tickets/{id} a livello router.

Coperture specifiche del wire format del router (non duplica le verifiche
RBAC del service, gia' coperte da test_tickets_rbac_contract.py):

  - sentinel `__UNSET__` per assignee: se la chiave manca dal JSON,
    il body Pydantic resta `__UNSET__` e il backend non tocca assignee
  - `assignee: null` esplicito nel JSON → unassign (passato come None)
  - status/severity/category invalidi → 400
  - title troppo corto → 400, troppo lungo → 400
  - admin update full passa al service
  - 404 su ticket inesistente
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shared.contracts.auth import CurrentUser
from app.core.tickets.router import _require_not_esecutore, router as tickets_router


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _user(role: str = "admin", user_id: str = "adm", group_id: str = "g1") -> CurrentUser:
    return CurrentUser(
        user_id=user_id, username=f"user-{user_id}",
        group_id=group_id, role=role,
    )


def _ticket() -> dict:
    return {
        "id": "t1", "kind": "user_support", "title": "t",
        "description": "", "severity": "medium", "status": "open",
        "category": "other", "created_by": "u1", "assignee": None,
        "group_id": "g1", "created_at": "2026-04-19T00:00:00Z",
        "updated_at": "2026-04-19T00:00:00Z", "resolved_at": None,
    }


def _make_client(current_user: CurrentUser) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[_require_not_esecutore] = lambda: current_user
    app.include_router(tickets_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def mock_tdb(monkeypatch):
    import app.db.tickets_db_service as real_tdb
    m = MagicMock(spec=real_tdb)
    m.get_ticket.return_value = _ticket()
    m.update_ticket.return_value = _ticket()
    m.list_attachments.return_value = []
    m.list_comments.return_value = []

    import app.core.tickets.router as router_mod
    import app.core.tickets.service as svc_mod
    monkeypatch.setattr(router_mod, "tdb", m)
    monkeypatch.setattr(svc_mod, "tdb", m)
    return m


# =============================================================================
# Sentinel __UNSET__ behavior
# =============================================================================


def test_patch_without_assignee_key_keeps_assignee_untouched(mock_tdb):
    """Se la chiave 'assignee' non e' nel JSON → backend non tocca il campo
    (passa __UNSET__ al DB layer)."""
    client = _make_client(_user("admin"))
    response = client.patch("/api/tickets/t1", json={"status": "in_progress"})
    assert response.status_code == 200
    kwargs = mock_tdb.update_ticket.call_args.kwargs
    assert kwargs["assignee"] == "__UNSET__"
    assert kwargs["status"] == "in_progress"


def test_patch_assignee_null_explicit_means_unassign(mock_tdb):
    """`assignee: null` nel JSON → unassign (None passato al DB)."""
    client = _make_client(_user("admin"))
    response = client.patch("/api/tickets/t1", json={"assignee": None})
    assert response.status_code == 200
    kwargs = mock_tdb.update_ticket.call_args.kwargs
    assert kwargs["assignee"] is None


def test_patch_assignee_string_assigns(mock_tdb):
    client = _make_client(_user("admin"))
    response = client.patch("/api/tickets/t1", json={"assignee": "u-target"})
    assert response.status_code == 200
    assert mock_tdb.update_ticket.call_args.kwargs["assignee"] == "u-target"


# =============================================================================
# Validazione enum
# =============================================================================


def test_patch_invalid_status_returns_400(mock_tdb):
    client = _make_client(_user("admin"))
    response = client.patch("/api/tickets/t1", json={"status": "BOGUS"})
    assert response.status_code == 400
    assert "status non valido" in response.json()["detail"]
    mock_tdb.update_ticket.assert_not_called()


def test_patch_invalid_severity_returns_400(mock_tdb):
    client = _make_client(_user("admin"))
    response = client.patch("/api/tickets/t1", json={"severity": "extreme"})
    assert response.status_code == 400
    mock_tdb.update_ticket.assert_not_called()


def test_patch_invalid_category_returns_400(mock_tdb):
    client = _make_client(_user("admin"))
    response = client.patch("/api/tickets/t1", json={"category": "made_up"})
    assert response.status_code == 400
    mock_tdb.update_ticket.assert_not_called()


# =============================================================================
# Validazione title
# =============================================================================


def test_patch_title_too_short_returns_400(mock_tdb):
    client = _make_client(_user("admin"))
    response = client.patch("/api/tickets/t1", json={"title": "ab"})
    assert response.status_code == 400
    assert "title troppo corto" in response.json()["detail"]


def test_patch_title_too_long_returns_400(mock_tdb):
    client = _make_client(_user("admin"))
    response = client.patch("/api/tickets/t1", json={"title": "x" * 201})
    assert response.status_code == 400


def test_patch_title_at_boundary_succeeds(mock_tdb):
    """Esattamente 200 char: ok."""
    client = _make_client(_user("admin"))
    response = client.patch("/api/tickets/t1", json={"title": "y" * 200})
    assert response.status_code == 200
    assert mock_tdb.update_ticket.call_args.kwargs["title"] == "y" * 200


# =============================================================================
# Full update + 404
# =============================================================================


def test_patch_admin_full_update_passes_through(mock_tdb):
    client = _make_client(_user("admin"))
    response = client.patch("/api/tickets/t1", json={
        "status": "in_progress",
        "severity": "high",
        "category": "auth",
        "assignee": "u-target",
        "title": "Titolo aggiornato",
        "description": "Nuova descrizione",
    })
    assert response.status_code == 200
    kwargs = mock_tdb.update_ticket.call_args.kwargs
    assert kwargs["status"] == "in_progress"
    assert kwargs["severity"] == "high"
    assert kwargs["category"] == "auth"
    assert kwargs["assignee"] == "u-target"
    assert kwargs["title"] == "Titolo aggiornato"
    assert kwargs["description"] == "Nuova descrizione"


def test_patch_missing_ticket_returns_404(mock_tdb):
    mock_tdb.get_ticket.return_value = None
    client = _make_client(_user("admin"))
    response = client.patch("/api/tickets/missing", json={"status": "closed"})
    assert response.status_code == 404
    mock_tdb.update_ticket.assert_not_called()


def test_patch_empty_body_is_noop(mock_tdb):
    """PATCH con body vuoto: tutti i campi None tranne assignee=__UNSET__.
    Il service comunque chiama update_ticket — il DB layer fa no-op."""
    client = _make_client(_user("admin"))
    response = client.patch("/api/tickets/t1", json={})
    assert response.status_code == 200
    kwargs = mock_tdb.update_ticket.call_args.kwargs
    assert kwargs["status"] is None
    assert kwargs["severity"] is None
    assert kwargs["category"] is None
    assert kwargs["assignee"] == "__UNSET__"
    assert kwargs["title"] is None
    assert kwargs["description"] is None
