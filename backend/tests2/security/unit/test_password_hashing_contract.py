"""
Contract test — migrazione hashing password bcrypt → Argon2id.

Copertura:
"""

from __future__ import annotations

from types import SimpleNamespace

import bcrypt
import pytest
from fastapi import HTTPException


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# Helpers


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    from app.core.auth import auth as auth_mod
    monkeypatch.setattr(auth_mod, "_check_rate_limit", lambda ip: None)
    monkeypatch.setattr(auth_mod, "_record_failed_login", lambda ip: None)
    monkeypatch.setattr(auth_mod, "_clear_failed_logins", lambda ip: None)


@pytest.fixture(autouse=True)
def _no_mfa(monkeypatch):
    # Disabilita MFA: il test si concentra sul path password/rehash.
    from app.core.auth import mfa as _mfa
    monkeypatch.setattr(_mfa, "is_enabled", lambda uid: False)


@pytest.fixture(autouse=True)
def _stub_create_access_token(monkeypatch):
    from app.core.auth import auth as auth_mod
    monkeypatch.setattr(
        auth_mod, "create_access_token",
        lambda **kw: "fake.jwt.token",
    )


def _fake_request():
    return SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers=SimpleNamespace(get=lambda k, default="": default),
    )


# hash_password / verify_password


def test_hash_password_produces_argon2id():
    from app.core.auth.auth import hash_password

    h = hash_password("somepassword123")
    assert h.startswith("$argon2id$"), (
        f"hash_password deve produrre Argon2id, ottenuto: {h[:20]}"
    )


def test_verify_password_argon2id_roundtrip():
    from app.core.auth.auth import hash_password, verify_password

    pwd = "correcthorsebatterystaple"
    h = hash_password(pwd)
    assert verify_password(pwd, h) is True
    assert verify_password("wrong_password", h) is False


def test_verify_password_legacy_bcrypt_accepted():
    """Utente legacy con hash bcrypt: verify deve accettarlo."""
    from app.core.auth.auth import verify_password

    pwd = "legacyuserpass12"
    legacy_hash = bcrypt.hashpw(pwd.encode(), bcrypt.gensalt(rounds=4)).decode()
    assert legacy_hash.startswith("$2")
    assert verify_password(pwd, legacy_hash) is True
    assert verify_password("wrong_pwd", legacy_hash) is False


def test_verify_password_corrupt_hash_returns_false_no_raise():
    from app.core.auth.auth import verify_password

    # Garbage vari: nessuno deve sollevare, tutti False.
    for bad in ["", "garbage", "$2b$invalid", "$argon2id$broken", "not_a_hash"]:
        assert verify_password("whatever", bad) is False, (
            f"verify_password ha leakato eccezione o True su hash corrotto: {bad!r}"
        )


def test_verify_password_none_type_returns_false():
    from app.core.auth.auth import verify_password

    # Paranoia: contratto dice str, ma se per sbaglio arriva None non deve crashare.
    assert verify_password("x", None) is False  # type: ignore[arg-type]


# needs_rehash


def test_needs_rehash_bcrypt_true():
    from app.core.auth.auth import needs_rehash

    legacy_hash = bcrypt.hashpw(b"x", bcrypt.gensalt(rounds=4)).decode()
    assert needs_rehash(legacy_hash) is True


def test_needs_rehash_argon2id_fresh_false():
    from app.core.auth.auth import hash_password, needs_rehash

    fresh = hash_password("x")
    assert needs_rehash(fresh) is False


def test_needs_rehash_corrupt_false():
    from app.core.auth.auth import needs_rehash

    # Hash corrotti o sconosciuti: niente rehash alla cieca.
    for bad in ["", "garbage", "$argon2id$broken", "$unknownalgo$...", None]:
        assert needs_rehash(bad) is False, (  # type: ignore[arg-type]
            f"needs_rehash ha ritornato True su input non gestibile: {bad!r}"
        )


# Login: rehash progressivo bcrypt → Argon2id


def test_login_rehashes_bcrypt_user_on_success(monkeypatch):
    """Utente con hash bcrypt legacy fa login con password giusta →
    update_user_password deve essere chiamato con un hash Argon2id."""
    from app.core.auth import auth as auth_mod

    pwd = "correctpass12345"
    legacy_hash = bcrypt.hashpw(pwd.encode(), bcrypt.gensalt(rounds=4)).decode()
    fake_user = {
        "id": "u1", "username": "legacy", "password_hash": legacy_hash,
        "group_id": "g1", "role": "user", "is_active": True,
    }
    monkeypatch.setattr(auth_mod.db, "get_user_by_username", lambda u: fake_user)

    captured = {}

    def fake_update(user_id, new_hash):
        captured["user_id"] = user_id
        captured["new_hash"] = new_hash

    monkeypatch.setattr(auth_mod.db, "update_user_password", fake_update)

    req = auth_mod.LoginRequest(username="legacy", password=pwd)
    resp = auth_mod.login(req, _fake_request())

    assert resp.access_token == "fake.jwt.token"
    assert captured.get("user_id") == "u1", (
        "Rehash NON eseguito: update_user_password mai chiamato per utente bcrypt."
    )
    assert captured["new_hash"].startswith("$argon2id$"), (
        f"Rehash non in Argon2id: {captured['new_hash'][:20]}"
    )


def test_login_does_not_rehash_argon2id_user(monkeypatch):
    """Utente gia' su Argon2id fresco: nessun UPDATE superfluo."""
    from app.core.auth import auth as auth_mod
    from app.core.auth.auth import hash_password

    pwd = "alreadyarg2id123"
    fresh_hash = hash_password(pwd)
    fake_user = {
        "id": "u2", "username": "modern", "password_hash": fresh_hash,
        "group_id": "g1", "role": "user", "is_active": True,
    }
    monkeypatch.setattr(auth_mod.db, "get_user_by_username", lambda u: fake_user)

    calls = {"n": 0}
    monkeypatch.setattr(
        auth_mod.db,
        "update_user_password",
        lambda *a, **kw: calls.__setitem__("n", calls["n"] + 1),
    )

    req = auth_mod.LoginRequest(username="modern", password=pwd)
    auth_mod.login(req, _fake_request())

    assert calls["n"] == 0, (
        "update_user_password chiamato inutilmente su utente gia' Argon2id."
    )


def test_login_does_not_rehash_on_wrong_password(monkeypatch):
    """Password sbagliata su utente bcrypt: 401 e zero rehash."""
    from app.core.auth import auth as auth_mod

    legacy_hash = bcrypt.hashpw(b"realpass123456", bcrypt.gensalt(rounds=4)).decode()
    fake_user = {
        "id": "u3", "username": "legacy2", "password_hash": legacy_hash,
        "group_id": "g1", "role": "user", "is_active": True,
    }
    monkeypatch.setattr(auth_mod.db, "get_user_by_username", lambda u: fake_user)

    calls = {"n": 0}
    monkeypatch.setattr(
        auth_mod.db,
        "update_user_password",
        lambda *a, **kw: calls.__setitem__("n", calls["n"] + 1),
    )

    req = auth_mod.LoginRequest(username="legacy2", password="wrongpwd12345")
    with pytest.raises(HTTPException) as excinfo:
        auth_mod.login(req, _fake_request())
    assert excinfo.value.status_code == 401
    assert calls["n"] == 0, (
        "Rehash eseguito su login fallito: bug potenzialmente serio."
    )


def test_login_rehash_best_effort_db_failure_does_not_block_login(monkeypatch):
    """Se il rehash UPDATE fallisce, il login deve comunque riuscire
    (best-effort). Al prossimo login riproveremo."""
    from app.core.auth import auth as auth_mod

    pwd = "correctpass12345"
    legacy_hash = bcrypt.hashpw(pwd.encode(), bcrypt.gensalt(rounds=4)).decode()
    fake_user = {
        "id": "u4", "username": "legacy3", "password_hash": legacy_hash,
        "group_id": "g1", "role": "user", "is_active": True,
    }
    monkeypatch.setattr(auth_mod.db, "get_user_by_username", lambda u: fake_user)

    def boom(*a, **kw):
        raise RuntimeError("DB down during rehash")

    monkeypatch.setattr(auth_mod.db, "update_user_password", boom)

    req = auth_mod.LoginRequest(username="legacy3", password=pwd)
    # Non deve sollevare: login va a buon fine anche con rehash fail.
    resp = auth_mod.login(req, _fake_request())
    assert resp.access_token == "fake.jwt.token"
