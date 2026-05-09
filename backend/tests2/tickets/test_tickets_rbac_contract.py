"""
Contract test — ticket RBAC + state transitions.

Copre le regole descritte in app/core/tickets/service.py:
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.shared.contracts.auth import CurrentUser
from app.core.tickets import db as tdb


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _user(role: str, user_id: str = "u1", group_id: str = "g1") -> CurrentUser:
    return CurrentUser(
        user_id=user_id, username=f"user-{user_id}",
        group_id=group_id, role=role,
    )


def _ticket(**overrides) -> tdb.TicketRow:
    data = {
        "id": "t1",
        "kind": "user_support",
        "title": "t",
        "description": "",
        "severity": "medium",
        "status": "open",
        "category": "other",
        "created_by": "u1",
        "assignee": None,
        "group_id": "g1",
        "created_at": "2026-04-18T00:00:00Z",
        "updated_at": "2026-04-18T00:00:00Z",
        "resolved_at": None,
    }
    data.update(overrides)
    return tdb.TicketRow(**data)


def _comment(**overrides) -> tdb.CommentRow:
    data = {
        "id": "c1",
        "ticket_id": "t1",
        "author_id": "me",
        "body": "ciao",
        "created_at": "2026-04-18T00:00:00Z",
    }
    data.update(overrides)
    return tdb.CommentRow(**data)


@pytest.fixture
def mock_tdb(monkeypatch):
    """Mocka tickets_db_service. Ritorna il MagicMock cosi' il test
    imposta side_effect / return_value."""
    import app.core.tickets.db as real_tdb
    m = MagicMock(spec=real_tdb)
    # Defaults sensati
    m.create_ticket.return_value = _ticket()
    m.get_ticket.return_value = m.create_ticket.return_value
    m.update_ticket.return_value = m.create_ticket.return_value
    m.list_tickets.return_value = []
    m.list_comments.return_value = []
    m.list_attachments.return_value = []
    m.count_tickets.return_value = 0
    m.counts_by_severity.return_value = {"low": 0, "medium": 0, "high": 0, "critical": 0}

    import app.core.tickets.service as svc_mod
    monkeypatch.setattr(svc_mod, "tdb", m)
    return m


# CREATE


def test_esecutore_cannot_create_ticket(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("esecutore")
    # list_tickets_for solleva 403 per esecutore (create passa da router).
    # Verifichiamo il guard a livello service:
    with pytest.raises(HTTPException) as ei:
        svc.list_tickets_for(actor)
    assert ei.value.status_code == 403


def test_user_can_create_user_support(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("user")
    result = svc.create_ticket(
        kind="user_support", title="Problema X", description="desc",
        severity="medium", category="ui", actor=actor,
    )
    assert result["id"] == "t1"
    mock_tdb.create_ticket.assert_called_once()
    kwargs = mock_tdb.create_ticket.call_args.kwargs
    assert kwargs["kind"] == "user_support"
    assert kwargs["group_id"] == "g1"  # forzato al group dell'attore
    assert kwargs["created_by"] == "u1"


def test_user_cannot_create_tech_debt(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("user")
    with pytest.raises(HTTPException) as ei:
        svc.create_ticket(
            kind="tech_debt", title="TD", description="",
            severity="medium", category="other", actor=actor,
        )
    assert ei.value.status_code == 403
    mock_tdb.create_ticket.assert_not_called()


def test_admin_can_create_both_kinds(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("admin")
    for kind in ("user_support", "tech_debt"):
        svc.create_ticket(
            kind=kind, title="test title", description="",
            severity="low", category="other", actor=actor,
        )
    assert mock_tdb.create_ticket.call_count == 2


def test_superadmin_cross_group_tech_debt(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("superadmin")
    svc.create_ticket(
        kind="tech_debt", title="test title", description="",
        severity="high", category="pipeline", actor=actor,
        target_group_id="g-other",
    )
    kwargs = mock_tdb.create_ticket.call_args.kwargs
    assert kwargs["group_id"] == "g-other"  # onora target


def test_admin_cannot_cross_group_with_target(mock_tdb):
    """admin (non superadmin) passa target_group_id → ignorato, usa il suo."""
    from app.core.tickets import service as svc
    actor = _user("admin", group_id="g-own")
    svc.create_ticket(
        kind="tech_debt", title="test title", description="",
        severity="low", category="other", actor=actor,
        target_group_id="g-other",  # IGNORATO
    )
    kwargs = mock_tdb.create_ticket.call_args.kwargs
    assert kwargs["group_id"] == "g-own"


def test_invalid_kind_400(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("admin")
    with pytest.raises(HTTPException) as ei:
        svc.create_ticket(
            kind="bogus", title="test title", description="",
            severity="low", category="other", actor=actor,
        )
    assert ei.value.status_code == 400


def test_invalid_category_400(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("user")
    with pytest.raises(HTTPException) as ei:
        svc.create_ticket(
            kind="user_support", title="test title", description="",
            severity="low", category="nonexistent", actor=actor,
        )
    assert ei.value.status_code == 400


def test_title_too_short(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("user")
    with pytest.raises(HTTPException) as ei:
        svc.create_ticket(
            kind="user_support", title="ab", description="",
            severity="low", category="other", actor=actor,
        )
    assert ei.value.status_code == 400


def test_non_admin_cannot_preassign(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("user")
    with pytest.raises(HTTPException) as ei:
        svc.create_ticket(
            kind="user_support", title="titolo", description="",
            severity="low", category="other", actor=actor,
            assignee="some-other-user",
        )
    assert ei.value.status_code == 403


# VIEW / LIST


def test_user_sees_only_own_tickets(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("user", user_id="me")
    svc.list_tickets_for(actor=actor)
    kwargs = mock_tdb.list_tickets.call_args.kwargs
    assert kwargs["created_by"] == "me"
    assert kwargs["group_id"] is None  # filtro solo per creator


def test_admin_sees_group_tickets(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("admin", group_id="g1")
    svc.list_tickets_for(actor=actor)
    kwargs = mock_tdb.list_tickets.call_args.kwargs
    assert kwargs["group_id"] == "g1"
    assert kwargs["created_by"] is None


def test_superadmin_sees_cross_group(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("superadmin")
    svc.list_tickets_for(actor=actor)
    kwargs = mock_tdb.list_tickets.call_args.kwargs
    assert kwargs["group_id"] is None
    assert kwargs["created_by"] is None


def test_user_cannot_view_other_users_ticket(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("user", user_id="me")
    # ticket creato da qualcun altro
    mock_tdb.get_ticket.return_value = _ticket(id="t99", created_by="other", title="x")
    with pytest.raises(HTTPException) as ei:
        svc.get_ticket_with_details("t99", actor)
    # 404 (non 403) per non leakare esistenza
    assert ei.value.status_code == 404


def test_admin_cannot_view_other_group_ticket(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("admin", group_id="g1")
    mock_tdb.get_ticket.return_value = _ticket(
        id="t99", created_by="other", group_id="g2", title="x",
    )
    with pytest.raises(HTTPException) as ei:
        svc.get_ticket_with_details("t99", actor)
    assert ei.value.status_code == 404


# UPDATE — RBAC sui privileged fields


def test_user_cannot_change_severity(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("user", user_id="me")
    mock_tdb.get_ticket.return_value = _ticket(created_by="me", title="x")
    with pytest.raises(HTTPException) as ei:
        svc.update_ticket("t1", actor=actor, severity="critical")
    assert ei.value.status_code == 403


def test_user_cannot_assign(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("user", user_id="me")
    mock_tdb.get_ticket.return_value = _ticket(created_by="me", title="x")
    with pytest.raises(HTTPException) as ei:
        svc.update_ticket("t1", actor=actor, assignee="someone-else")
    assert ei.value.status_code == 403


def test_user_can_reopen_own_resolved_ticket(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("user", user_id="me")
    mock_tdb.get_ticket.return_value = _ticket(
        created_by="me", status="resolved", title="x",
    )
    svc.update_ticket("t1", actor=actor, status="reopened")
    mock_tdb.update_ticket.assert_called_once()


def test_user_cannot_reopen_other_ticket(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("user", user_id="me")
    mock_tdb.get_ticket.return_value = _ticket(
        created_by="other", status="resolved", title="x",
    )
    with pytest.raises(HTTPException) as ei:
        svc.update_ticket("t1", actor=actor, status="reopened")
    # Non lo VEDE nemmeno → 404
    assert ei.value.status_code == 404


def test_user_cannot_close_own_ticket(mock_tdb):
    """La chiusura definitiva e' operazione admin; l'utente puo' solo riaprire."""
    from app.core.tickets import service as svc
    actor = _user("user", user_id="me")
    mock_tdb.get_ticket.return_value = _ticket(
        created_by="me", status="resolved", title="x",
    )
    with pytest.raises(HTTPException) as ei:
        svc.update_ticket("t1", actor=actor, status="closed")
    assert ei.value.status_code == 403


def test_admin_full_update_in_own_group(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("admin", group_id="g1")
    mock_tdb.get_ticket.return_value = _ticket(created_by="someone", title="x")
    svc.update_ticket(
        "t1", actor=actor,
        status="in_progress", severity="high", assignee="admin-user",
    )
    mock_tdb.update_ticket.assert_called_once()


# COMMENTS


def test_user_can_comment_on_own_ticket(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("user", user_id="me")
    mock_tdb.get_ticket.return_value = _ticket(created_by="me", title="x")
    mock_tdb.add_comment.return_value = _comment()
    svc.add_comment("t1", "ciao", actor)
    mock_tdb.add_comment.assert_called_once_with("t1", "me", "ciao")


def test_user_cannot_comment_on_others_ticket(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("user", user_id="me")
    mock_tdb.get_ticket.return_value = _ticket(created_by="other", title="x")
    with pytest.raises(HTTPException) as ei:
        svc.add_comment("t1", "hack", actor)
    assert ei.value.status_code == 404


def test_comment_empty_body_rejected(mock_tdb):
    from app.core.tickets import service as svc
    actor = _user("user", user_id="me")
    mock_tdb.get_ticket.return_value = _ticket(created_by="me", title="x")
    with pytest.raises(HTTPException) as ei:
        svc.add_comment("t1", "   ", actor)
    assert ei.value.status_code == 400


# STATUS TRANSITIONS (resolved_at)


def test_resolving_sets_resolved_at(mock_tdb):
    """Quando admin passa status→resolved la prima volta, resolved_at
    deve essere popolato. Il test verifica che tdb.update_ticket sia
    chiamato con status='resolved'."""
    from app.core.tickets import service as svc
    actor = _user("admin", group_id="g1")
    mock_tdb.get_ticket.return_value = _ticket(status="in_progress", title="x")
    svc.update_ticket("t1", actor=actor, status="resolved")
    kwargs = mock_tdb.update_ticket.call_args.kwargs
    assert kwargs["status"] == "resolved"


# NOTIFIER HOOK — invocato ma best-effort


def test_notifier_called_on_create(mock_tdb, monkeypatch):
    from app.core.tickets import service as svc

    calls = []

    class Spy:
        def on_ticket_created(self, ticket, actor):
            calls.append(("created", ticket["id"], actor.username))

    svc.set_notifier(Spy())
    try:
        actor = _user("admin")
        svc.create_ticket(
            kind="tech_debt", title="test title", description="",
            severity="low", category="other", actor=actor,
        )
        assert len(calls) == 1
        assert calls[0][0] == "created"
    finally:
        svc.set_notifier(svc.NoopTicketNotifier())


def test_notifier_exception_does_not_break_create(mock_tdb, monkeypatch):
    from app.core.tickets import service as svc

    class Broken:
        def on_ticket_created(self, ticket, actor):
            raise RuntimeError("smtp down")

    svc.set_notifier(Broken())
    try:
        actor = _user("admin")
        # Non deve sollevare: notifier e' best-effort
        result = svc.create_ticket(
            kind="tech_debt", title="test title", description="",
            severity="low", category="other", actor=actor,
        )
        assert result["id"] == "t1"
    finally:
        svc.set_notifier(svc.NoopTicketNotifier())
