"""Contract test C10 — timing parity login + enumeration admin.

Scenari:
1. LOGIN timing parity: verify_password viene chiamato sia quando l'utente
   esiste sia quando non esiste. Altrimenti un attaccante misura il tempo
   di risposta 401 e distingue hit/miss → user enumeration.

2. ADMIN endpoint enumeration: reset-password e delete-user per admin
   non-superadmin NON devono distinguere tra "utente non esiste" e
   "utente in altro gruppo". Entrambi i casi → 403. (Superadmin puo'
   ricevere 404 perche' ha visibilita' totale, nessun enumeration).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# =============================================================================
# Login timing parity
# =============================================================================


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """Disabilita rate limit per isolare i test al timing/enumeration."""
    from app.core.auth import auth as auth_mod
    monkeypatch.setattr(auth_mod, "_check_rate_limit", lambda ip: None)
    monkeypatch.setattr(auth_mod, "_record_failed_login", lambda ip: None)
    monkeypatch.setattr(auth_mod, "_clear_failed_logins", lambda ip: None)


def _fake_request():
    # get_client_ip (reperto #27) legge anche request.headers: aggiungiamo
    # un mock compatibile per non AttributeError.
    return SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers=SimpleNamespace(get=lambda k, default="": default),
    )


def test_login_calls_verify_password_even_when_user_missing(monkeypatch):
    """C10: quando il DB non trova l'username, login DEVE comunque chiamare
    verify_password (con dummy hash). Altrimenti il timing differenziale
    rivela l'esistenza dell'utente."""
    from app.core.auth import auth as auth_mod

    call_count = {"n": 0}

    def spy_verify(pwd, hashed):
        call_count["n"] += 1
        return False  # password sempre sbagliata (non rilevante)

    monkeypatch.setattr(auth_mod.db, "get_user_by_username", lambda u: None)
    monkeypatch.setattr(auth_mod, "verify_password", spy_verify)

    req = auth_mod.LoginRequest(username="ghost_user", password="whatever1234")
    with pytest.raises(HTTPException) as excinfo:
        auth_mod.login(req, _fake_request())
    assert excinfo.value.status_code == 401
    assert call_count["n"] == 1, (
        "REGRESSIONE C10: verify_password NON chiamato quando user missing "
        "→ short-circuit rivela non-esistenza utente via timing."
    )


def test_login_calls_verify_password_when_user_exists(monkeypatch):
    """Controllo simmetrico: il flusso con user esistente chiama
    verify_password (comportamento atteso sempre)."""
    from app.core.auth import auth as auth_mod

    call_count = {"n": 0}

    def spy_verify(pwd, hashed):
        call_count["n"] += 1
        return False  # password sbagliata

    fake_user = {
        "id": "u1", "username": "real", "password_hash": "$2b$12$hash",
        "group_id": "g1", "role": "user", "is_active": True,
    }
    monkeypatch.setattr(auth_mod.db, "get_user_by_username", lambda u: fake_user)
    monkeypatch.setattr(auth_mod, "verify_password", spy_verify)

    req = auth_mod.LoginRequest(username="real", password="wrong1234567")
    with pytest.raises(HTTPException) as excinfo:
        auth_mod.login(req, _fake_request())
    assert excinfo.value.status_code == 401
    assert call_count["n"] == 1


def test_login_uses_real_argon2id_dummy_hash():
    """Il _DUMMY_PASSWORD_HASH deve essere un hash Argon2id VALIDO (post
    migrazione bcrypt→Argon2id). Altrimenti verify_password solleverebbe
    su hash corrotto e il timing divergerebbe comunque."""
    from app.core.auth.auth import _DUMMY_PASSWORD_HASH, verify_password

    # Deve iniziare con il prefix Argon2id
    assert _DUMMY_PASSWORD_HASH.startswith("$argon2id$"), (
        f"_DUMMY_PASSWORD_HASH non e' argon2id: {_DUMMY_PASSWORD_HASH[:15]}"
    )
    # verify_password non deve sollevare su questo hash
    # (password random sbagliata → deve ritornare False pulito)
    result = verify_password("random_wrong_pwd", _DUMMY_PASSWORD_HASH)
    assert result is False


# =============================================================================
# Admin endpoint enumeration (reset-password + delete-user)
# =============================================================================


def _admin_user(group="g1"):
    return SimpleNamespace(
        user_id="admin1", username="admin", group_id=group, role="admin",
    )


def _superadmin_user():
    return SimpleNamespace(
        user_id="root", username="root", group_id="", role="superadmin",
    )


def test_reset_password_nonsuperadmin_user_not_found_returns_403(monkeypatch):
    """C10: admin non-superadmin su user_id inesistente DEVE ricevere 403,
    non 404. Altrimenti puo' enumerare utenti del gruppo altrui."""
    from app.core.auth import auth as auth_mod

    monkeypatch.setattr(auth_mod.db, "get_user", lambda uid: None)

    data = auth_mod.ResetPasswordRequest(new_password="newsecret12345")
    with pytest.raises(HTTPException) as excinfo:
        auth_mod.reset_password("nonexistent", data, _admin_user())
    assert excinfo.value.status_code == 403, (
        f"REGRESSIONE C10: user missing → {excinfo.value.status_code}, "
        f"admin puo' distinguere missing vs other-group."
    )


def test_reset_password_nonsuperadmin_user_other_group_returns_403(monkeypatch):
    """Controllo simmetrico: user esiste ma in altro gruppo → 403."""
    from app.core.auth import auth as auth_mod

    other_user = {"id": "x", "group_id": "g_other", "username": "x", "is_active": True}
    monkeypatch.setattr(auth_mod.db, "get_user", lambda uid: other_user)

    data = auth_mod.ResetPasswordRequest(new_password="newsecret12345")
    with pytest.raises(HTTPException) as excinfo:
        auth_mod.reset_password("x", data, _admin_user())
    assert excinfo.value.status_code == 403


def test_reset_password_superadmin_user_not_found_returns_404(monkeypatch):
    """Superadmin: 404 accettabile — ha visibilita' totale, nessun
    enumeration (non ci sono utenti "nascosti")."""
    from app.core.auth import auth as auth_mod

    monkeypatch.setattr(auth_mod.db, "get_user", lambda uid: None)

    data = auth_mod.ResetPasswordRequest(new_password="newsecret12345")
    with pytest.raises(HTTPException) as excinfo:
        auth_mod.reset_password("nonexistent", data, _superadmin_user())
    assert excinfo.value.status_code == 404


def test_delete_user_nonsuperadmin_user_not_found_returns_403(monkeypatch):
    """delete-user: stesso pattern di reset-password."""
    from app.core.auth import auth as auth_mod

    monkeypatch.setattr(auth_mod.db, "get_user", lambda uid: None)

    with pytest.raises(HTTPException) as excinfo:
        auth_mod.delete_user("nonexistent", _admin_user())
    assert excinfo.value.status_code == 403


def test_delete_user_nonsuperadmin_user_other_group_returns_403(monkeypatch):
    from app.core.auth import auth as auth_mod

    other_user = {"id": "x", "group_id": "g_other", "username": "x", "is_active": True}
    monkeypatch.setattr(auth_mod.db, "get_user", lambda uid: other_user)

    with pytest.raises(HTTPException) as excinfo:
        auth_mod.delete_user("x", _admin_user())
    assert excinfo.value.status_code == 403
