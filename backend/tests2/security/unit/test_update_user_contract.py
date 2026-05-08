"""
Contract test — invariants di PUT /users/{user_id}.

Endpoint `update_user` (auth.py:721) NON deve:
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from dataclasses import replace

from app.core.auth.db import GroupRow, UserRow


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# ── Fixture utenti mock ───────────────────────────────────────────────────


def _admin_user(group_id="g1"):
    return SimpleNamespace(
        user_id="usr_admin", username="admin_u",
        group_id=group_id, role="admin",
    )


def _superadmin_user():
    return SimpleNamespace(
        user_id="usr_super", username="super",
        group_id="g_admin", role="superadmin",
    )


def _target_user(group_id="g1", user_id="usr_target"):
    return UserRow(
        id=user_id,
        username="mario",
        email="m@x.it",
        role="user",
        group_id=group_id,
        is_active=True,
        password_hash="$2b$12$fakehashforbctest",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )


@pytest.fixture
def mock_db(monkeypatch):
    """Monkeypatcha db.get_user/update_user/get_group per isolare il test.

    BE-GO-READY-AUTH-ENGINE-021: il fixture restituisce
    `app.core.auth.users_router` (canonical handler home post-
    AUTH-ENGINE-003 extraction) invece del facade `auth`. La firma
    dei test (`mock_db.update_user(...)`) e' invariata; cambia
    solo il modulo cui si attaccano i monkeypatch.
    """
    from app.core.auth import users_router as users_mod

    target = _target_user()
    monkeypatch.setattr(users_mod._users_dal, "get_user", lambda uid: target)
    # update_user: echo dei campi modificati sul target
    def _upd(uid, **fields):
        return replace(target, **fields)
    monkeypatch.setattr(users_mod._users_dal, "update_user", _upd)
    # get_group: sempre esistente
    monkeypatch.setattr(
        users_mod._groups_dal,
        "get_group",
        lambda gid: GroupRow(id=gid, name="fake"),
    )
    return users_mod


# ── 1. password rifiutata ─────────────────────────────────────────────────


def test_update_user_rejects_password_in_payload(mock_db):
    """PUT /users/{id} con `password` deve dare 400 esplicito invece di
    scartarla silenziosamente (bug storico)."""
    with pytest.raises(HTTPException) as exc:
        mock_db.update_user(
            user_id="usr_target",
            data={"password": "nuova_password_1234"},
            current_user=_admin_user(),
        )
    assert exc.value.status_code == 400
    assert "reset-password" in exc.value.detail.lower()


def test_update_user_rejects_password_even_from_superadmin(mock_db):
    """Il guard vale per chiunque: anche superadmin deve usare
    /reset-password per cambiare password. Evita drift."""
    with pytest.raises(HTTPException) as exc:
        mock_db.update_user(
            user_id="usr_target",
            data={"password": "x" * 20},
            current_user=_superadmin_user(),
        )
    assert exc.value.status_code == 400


# ── 2. group_id rifiutato per admin ────────────────────────────────────────


def test_update_user_rejects_group_id_from_admin(mock_db):
    """admin (non-superadmin) che tenta di cambiare group_id -> 403."""
    with pytest.raises(HTTPException) as exc:
        mock_db.update_user(
            user_id="usr_target",
            data={"group_id": "altro_gruppo"},
            current_user=_admin_user(),
        )
    assert exc.value.status_code == 403


def test_update_user_accepts_group_id_from_superadmin(mock_db):
    """superadmin puo' cambiare group_id."""
    result = mock_db.update_user(
        user_id="usr_target",
        data={"group_id": "nuovo_gruppo"},
        current_user=_superadmin_user(),
    )
    # L'endpoint ritorna UserResponse, che e' un model pydantic
    assert result.group_id == "nuovo_gruppo"


# ── 3. role validation ────────────────────────────────────────────────────


def test_update_user_rejects_invalid_role(mock_db):
    with pytest.raises(HTTPException) as exc:
        mock_db.update_user(
            user_id="usr_target",
            data={"role": "god_mode"},
            current_user=_superadmin_user(),
        )
    assert exc.value.status_code == 400
    assert "ruolo non valido" in exc.value.detail.lower()


# ── 4/5. campi legittimi passano ───────────────────────────────────────────


def test_update_user_accepts_email_from_admin(mock_db):
    result = mock_db.update_user(
        user_id="usr_target",
        data={"email": "new@example.com"},
        current_user=_admin_user(),
    )
    assert result.email == "new@example.com"


def test_update_user_accepts_is_active_from_admin(mock_db):
    result = mock_db.update_user(
        user_id="usr_target",
        data={"is_active": False},
        current_user=_admin_user(),
    )
    assert result.is_active is False


def test_update_user_accepts_role_from_superadmin(mock_db):
    result = mock_db.update_user(
        user_id="usr_target",
        data={"role": "viewer"},
        current_user=_superadmin_user(),
    )
    assert result.role == "viewer"


# ── 6. cross-group access denied ───────────────────────────────────────────


def test_update_user_denies_cross_group_access(monkeypatch):
    """admin di gruppo A non puo' modificare utente di gruppo B.

    BE-GO-READY-AUTH-ENGINE-021: `update_user` no longer re-exported
    from `app.core.auth.auth`. Call via `app.core.auth.users_router`
    (canonical handler home since AUTH-ENGINE-003 extraction).
    """
    from app.core.auth import users_router as users_mod

    target_in_other_group = _target_user(group_id="gruppo_b", user_id="usr_b")
    monkeypatch.setattr(
        users_mod._users_dal,
        "get_user",
        lambda uid: target_in_other_group,
    )

    admin_of_a = _admin_user(group_id="gruppo_a")
    with pytest.raises(HTTPException) as exc:
        users_mod.update_user(
            user_id="usr_b",
            data={"email": "new@x.it"},
            current_user=admin_of_a,
        )
    assert exc.value.status_code == 403
