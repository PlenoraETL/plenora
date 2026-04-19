"""Contract test — endpoints /tickets/meta e /tickets/counts.

Coperture:
  - meta: contiene tutte le enum + allowlist allegati + size cap
  - counts superadmin → cross-group, by_severity popolato
  - counts admin → filtra per group_id, by_severity popolato
  - counts user → ritorna `active_mine` calcolato sui propri ticket
  - meta/counts: esecutore → 403
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.auth.auth import CurrentUser
from app.core.tickets.router import _require_not_esecutore, router as tickets_router


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _user(role: str = "user", user_id: str = "u1", group_id: str = "g1") -> CurrentUser:
    return CurrentUser(
        user_id=user_id, username=f"user-{user_id}",
        group_id=group_id, role=role,
    )


def _make_client(current_user: CurrentUser) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[_require_not_esecutore] = lambda: current_user
    app.include_router(tickets_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def mock_tdb(monkeypatch):
    import app.db.tickets_db_service as real_tdb
    m = MagicMock(spec=real_tdb)
    m.count_tickets.return_value = 0
    m.counts_by_severity.return_value = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    m.list_tickets.return_value = []

    import app.core.tickets.router as router_mod
    import app.core.tickets.service as svc_mod
    monkeypatch.setattr(router_mod, "tdb", m)
    monkeypatch.setattr(svc_mod, "tdb", m)
    return m


# =============================================================================
# META
# =============================================================================


def test_meta_returns_all_enums(mock_tdb):
    client = _make_client(_user("user"))
    response = client.get("/api/tickets/meta")
    assert response.status_code == 200
    body = response.json()

    # kinds
    assert set(body["kinds"]) == {"tech_debt", "user_support"}
    # severities
    assert set(body["severities"]) == {"low", "medium", "high", "critical"}
    # statuses (insieme completo)
    assert "open" in body["statuses"]
    assert "in_progress" in body["statuses"]
    assert "waiting_user" in body["statuses"]
    assert "resolved" in body["statuses"]
    assert "closed" in body["statuses"]
    assert "reopened" in body["statuses"]
    # statuses_active sottoinsieme degli statuses
    assert set(body["statuses_active"]).issubset(set(body["statuses"]))
    # categorie come [{value, label}]
    assert isinstance(body["categories"], list)
    assert all("value" in c and "label" in c for c in body["categories"])
    # allowlist allegati
    assert "png" in body["allowed_attachment_extensions"]
    assert "log" in body["allowed_attachment_extensions"]
    assert "exe" not in body["allowed_attachment_extensions"]
    # size cap = 10MB
    assert body["max_attachment_size_bytes"] == 10 * 1024 * 1024


def test_meta_categories_contains_expected_values(mock_tdb):
    client = _make_client(_user("admin"))
    response = client.get("/api/tickets/meta")
    cats = {c["value"] for c in response.json()["categories"]}
    # Whitelist da TICKET_CATEGORIES (service.py)
    expected = {"auth", "pipeline", "ui", "performance", "data_quality", "deploy", "other"}
    assert expected.issubset(cats)


# =============================================================================
# COUNTS
# =============================================================================


def test_counts_superadmin_is_cross_group(mock_tdb):
    """superadmin: niente filtro group_id, by_severity popolato."""
    mock_tdb.count_tickets.return_value = 7
    mock_tdb.counts_by_severity.return_value = {
        "low": 1, "medium": 3, "high": 2, "critical": 1,
    }
    client = _make_client(_user("superadmin", user_id="sa", group_id="gX"))
    response = client.get("/api/tickets/counts")
    assert response.status_code == 200
    body = response.json()
    assert body["active_total"] == 7
    assert body["by_severity"]["critical"] == 1
    # Verifica scope: nessun group_id passato al DB
    args = mock_tdb.count_tickets.call_args_list[0]
    assert args.kwargs.get("group_id") is None
    sev_args = mock_tdb.counts_by_severity.call_args
    assert sev_args.kwargs.get("group_id") is None


def test_counts_admin_filters_by_own_group(mock_tdb):
    """admin: counts ristretti al proprio group_id."""
    mock_tdb.count_tickets.return_value = 3
    mock_tdb.counts_by_severity.return_value = {
        "low": 0, "medium": 1, "high": 2, "critical": 0,
    }
    client = _make_client(_user("admin", user_id="adm1", group_id="g1"))
    response = client.get("/api/tickets/counts")
    assert response.status_code == 200
    body = response.json()
    assert body["active_total"] == 3
    assert body["by_severity"]["high"] == 2
    # Verifica filtro group_id
    for call in mock_tdb.count_tickets.call_args_list:
        assert call.kwargs.get("group_id") == "g1"
    assert mock_tdb.counts_by_severity.call_args.kwargs.get("group_id") == "g1"


def test_counts_admin_returns_assigned_to_me(mock_tdb):
    """admin: campo `active_assigned_to_me` popolato col proprio user_id."""
    mock_tdb.count_tickets.side_effect = [10, 4]  # active_total, assigned_to_me
    mock_tdb.counts_by_severity.return_value = {
        "low": 0, "medium": 0, "high": 0, "critical": 0,
    }
    client = _make_client(_user("admin", user_id="adm1", group_id="g1"))
    response = client.get("/api/tickets/counts")
    body = response.json()
    assert body["active_total"] == 10
    assert body["active_assigned_to_me"] == 4
    # La seconda chiamata count_tickets passa assignee=user_id
    assigned_call = mock_tdb.count_tickets.call_args_list[1]
    assert assigned_call.kwargs.get("assignee") == "adm1"


def test_counts_user_returns_active_mine_only(mock_tdb):
    """user/viewer: ritorna `active_mine` (count dei suoi ticket attivi)."""
    # list_tickets restituisce 2 ticket per `user`
    mock_tdb.list_tickets.return_value = [
        {"id": "t1", "status": "open"},
        {"id": "t2", "status": "in_progress"},
    ]
    client = _make_client(_user("user", user_id="u1", group_id="g1"))
    response = client.get("/api/tickets/counts")
    assert response.status_code == 200
    body = response.json()
    assert body["active_mine"] == 2
    # by_severity vuoto per user (no breakdown)
    assert body["by_severity"] == {}
    # list_tickets chiamato con created_by=u1 (RBAC user scope)
    assert mock_tdb.list_tickets.call_args.kwargs.get("created_by") == "u1"


def test_counts_user_with_zero_tickets(mock_tdb):
    mock_tdb.list_tickets.return_value = []
    client = _make_client(_user("user"))
    response = client.get("/api/tickets/counts")
    assert response.json()["active_mine"] == 0


def test_counts_filters_only_active_statuses(mock_tdb):
    """Sia per admin che superadmin, status_in deve contenere SOLO active."""
    mock_tdb.count_tickets.return_value = 0
    mock_tdb.counts_by_severity.return_value = {}
    client = _make_client(_user("admin", group_id="g1"))
    client.get("/api/tickets/counts")
    status_in = mock_tdb.count_tickets.call_args.kwargs.get("status_in")
    assert status_in is not None
    # Active esclude resolved + closed
    assert "resolved" not in status_in
    assert "closed" not in status_in
    assert "open" in status_in
    assert "in_progress" in status_in


# =============================================================================
# RBAC esecutore
# =============================================================================


def test_meta_blocks_esecutore():
    """L'override di _require_not_esecutore non scatta nei test (e' bypassato),
    ma se simuliamo il guard reale, esecutore deve ricevere 403."""
    app = FastAPI()

    def _block_esecutore() -> CurrentUser:
        raise HTTPException(403, "Accesso ai ticket non consentito")

    app.dependency_overrides[_require_not_esecutore] = _block_esecutore
    app.include_router(tickets_router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/tickets/meta")
    assert response.status_code == 403


def test_counts_blocks_esecutore():
    app = FastAPI()

    def _block_esecutore() -> CurrentUser:
        raise HTTPException(403, "Accesso ai ticket non consentito")

    app.dependency_overrides[_require_not_esecutore] = _block_esecutore
    app.include_router(tickets_router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/tickets/counts")
    assert response.status_code == 403
