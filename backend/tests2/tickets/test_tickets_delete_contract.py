"""Contract test — DELETE /api/tickets/{id}.

Default = soft delete (set deleted_at, audit preservato).
`?hard=true` = purge definitivo via FK cascade (solo superadmin).

Coperture:
  - SOFT (default):
    - admin del gruppo: 200 + soft_delete_ticket chiamato + niente cleanup blob
    - superadmin: 200 cross-group, soft path
    - admin di altro gruppo: 404 (no info leak)
    - user (anche creator): 403
    - esecutore: 403 (filtrato da _require_not_esecutore)
    - ticket inesistente: 404
  - HARD (?hard=true):
    - superadmin: 200 + delete_ticket cascade + cleanup blob loop
    - admin (non superadmin): 403
    - cleanup failure best-effort
    - hard su ticket senza allegati skip cleanup
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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


def _ticket(group_id: str = "g1", created_by: str = "u1") -> dict:
    return {
        "id": "t1", "kind": "user_support", "title": "t",
        "description": "", "severity": "medium", "status": "open",
        "category": "other", "created_by": created_by, "assignee": None,
        "group_id": group_id, "created_at": "2026-04-19T00:00:00Z",
        "updated_at": "2026-04-19T00:00:00Z", "resolved_at": None,
        "deleted_at": None,
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
    m.list_attachments.return_value = []
    m.soft_delete_ticket.return_value = True
    m.delete_ticket.return_value = True

    import app.core.tickets.router as router_mod
    import app.core.tickets.service as svc_mod
    monkeypatch.setattr(router_mod, "tdb", m)
    monkeypatch.setattr(svc_mod, "tdb", m)
    return m


@pytest.fixture
def mock_file_service():
    fake_fs = MagicMock()
    fake_fs.delete_file_source.return_value = True
    with patch(
        "app.services.file_service.FileSourceService", return_value=fake_fs,
    ):
        yield fake_fs


# =============================================================================
# SOFT DELETE (default)
# =============================================================================


def test_admin_soft_deletes_own_group_ticket(mock_tdb, mock_file_service):
    """F2-B3 (2026-04-24): DELETE /api/tickets/{id} risponde 204 No
    Content. Semantica soft/hard verificata via mock call (il body
    {"ok": True, "hard": bool} esposto pre-fix e' rimosso)."""
    client = _make_client(_user("admin", user_id="adm", group_id="g1"))
    response = client.delete("/api/tickets/t1")
    assert response.status_code == 204
    assert response.content == b""
    # Semantica soft-delete verificata via dispatch al DB
    mock_tdb.soft_delete_ticket.assert_called_once_with("t1")
    mock_tdb.delete_ticket.assert_not_called()
    mock_file_service.delete_file_source.assert_not_called()


def test_superadmin_soft_deletes_cross_group(mock_tdb, mock_file_service):
    mock_tdb.get_ticket.return_value = _ticket(group_id="g1", created_by="u1")
    client = _make_client(_user("superadmin", user_id="sa", group_id="gX"))
    response = client.delete("/api/tickets/t1")
    assert response.status_code == 204
    # hard=False verificato via dispatch: soft_delete_ticket chiamato,
    # delete_ticket NON chiamato (il body echo "hard: false" e' rimosso).
    mock_tdb.soft_delete_ticket.assert_called_once_with("t1")
    mock_tdb.delete_ticket.assert_not_called()


def test_soft_delete_does_not_cleanup_blob(mock_tdb, mock_file_service):
    """Soft delete preserva file_source per recovery — no cleanup S3."""
    mock_tdb.list_attachments.return_value = [
        {"id": "att-1", "file_source_id": "fs-1"},
        {"id": "att-2", "file_source_id": "fs-2"},
    ]
    client = _make_client(_user("admin", group_id="g1"))
    response = client.delete("/api/tickets/t1")
    assert response.status_code == 204
    mock_file_service.delete_file_source.assert_not_called()


# =============================================================================
# HARD DELETE (?hard=true)
# =============================================================================


def test_superadmin_hard_deletes_with_cascade(mock_tdb, mock_file_service):
    """F2-B3: hard=True verificato via dispatch (body echo "hard: true"
    rimosso)."""
    mock_tdb.list_attachments.return_value = [
        {"id": "att-1", "file_source_id": "fs-1"},
        {"id": "att-2", "file_source_id": "fs-2"},
        {"id": "att-3", "file_source_id": "fs-3"},
    ]
    client = _make_client(_user("superadmin", user_id="sa"))
    response = client.delete("/api/tickets/t1?hard=true")
    assert response.status_code == 204
    # hard=True verificato via dispatch: delete_ticket chiamato,
    # soft_delete_ticket NON chiamato.
    mock_tdb.delete_ticket.assert_called_once_with("t1")
    mock_tdb.soft_delete_ticket.assert_not_called()
    # Tutti e 3 i file_source cleanati
    called = [c.args[0] for c in mock_file_service.delete_file_source.call_args_list]
    assert called == ["fs-1", "fs-2", "fs-3"]


def test_admin_cannot_hard_delete(mock_tdb, mock_file_service):
    """Hard delete riservato a superadmin: admin gruppo riceve 403."""
    client = _make_client(_user("admin", user_id="adm", group_id="g1"))
    response = client.delete("/api/tickets/t1?hard=true")
    assert response.status_code == 403
    mock_tdb.delete_ticket.assert_not_called()
    mock_tdb.soft_delete_ticket.assert_not_called()


def test_hard_delete_cleanup_failure_does_not_block(mock_tdb, mock_file_service):
    mock_tdb.list_attachments.return_value = [
        {"id": "att-1", "file_source_id": "fs-1"},
        {"id": "att-2", "file_source_id": "fs-2"},
    ]
    mock_file_service.delete_file_source.side_effect = [
        RuntimeError("S3 timeout"), True,
    ]
    client = _make_client(_user("superadmin"))
    response = client.delete("/api/tickets/t1?hard=true")
    assert response.status_code == 204
    assert mock_file_service.delete_file_source.call_count == 2


def test_hard_delete_with_no_attachments_skips_cleanup(mock_tdb, mock_file_service):
    mock_tdb.list_attachments.return_value = []
    client = _make_client(_user("superadmin"))
    response = client.delete("/api/tickets/t1?hard=true")
    assert response.status_code == 204
    mock_file_service.delete_file_source.assert_not_called()


def test_hard_delete_db_returns_false_propagates_404(mock_tdb, mock_file_service):
    """Race: ticket sparito tra get_ticket e delete → 404."""
    mock_tdb.delete_ticket.return_value = False
    client = _make_client(_user("superadmin"))
    response = client.delete("/api/tickets/t1?hard=true")
    assert response.status_code == 404


def test_soft_delete_db_returns_false_propagates_404(mock_tdb, mock_file_service):
    mock_tdb.soft_delete_ticket.return_value = False
    client = _make_client(_user("admin", group_id="g1"))
    response = client.delete("/api/tickets/t1")
    assert response.status_code == 404


# =============================================================================
# RBAC comuni (sia soft che hard)
# =============================================================================


def test_admin_other_group_gets_404(mock_tdb, mock_file_service):
    mock_tdb.get_ticket.return_value = _ticket(group_id="g1", created_by="u1")
    client = _make_client(_user("admin", user_id="adm", group_id="g2"))
    response = client.delete("/api/tickets/t1")
    assert response.status_code == 404
    mock_tdb.soft_delete_ticket.assert_not_called()


def test_user_creator_cannot_delete_own_ticket(mock_tdb, mock_file_service):
    mock_tdb.get_ticket.return_value = _ticket(group_id="g1", created_by="u1")
    client = _make_client(_user("user", user_id="u1", group_id="g1"))
    response = client.delete("/api/tickets/t1")
    assert response.status_code == 403
    mock_tdb.soft_delete_ticket.assert_not_called()


def test_user_other_cannot_delete(mock_tdb, mock_file_service):
    """User u2 non vede ticket di u1 → 404."""
    mock_tdb.get_ticket.return_value = _ticket(group_id="g1", created_by="u1")
    client = _make_client(_user("user", user_id="u2", group_id="g1"))
    response = client.delete("/api/tickets/t1")
    assert response.status_code == 404
    mock_tdb.soft_delete_ticket.assert_not_called()


def test_esecutore_blocked():
    app = FastAPI()

    def _block_esecutore() -> CurrentUser:
        raise HTTPException(403, "Accesso ai ticket non consentito")

    app.dependency_overrides[_require_not_esecutore] = _block_esecutore
    app.include_router(tickets_router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=False)
    response = client.delete("/api/tickets/t1")
    assert response.status_code == 403


def test_delete_missing_ticket_returns_404(mock_tdb, mock_file_service):
    mock_tdb.get_ticket.return_value = None
    client = _make_client(_user("admin", group_id="g1"))
    response = client.delete("/api/tickets/missing")
    assert response.status_code == 404
    mock_tdb.soft_delete_ticket.assert_not_called()
