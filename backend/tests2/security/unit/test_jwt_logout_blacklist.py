"""
Unit tests — JWT jti claim + /auth/logout + blacklist Redis.

Reperto coperto: revoca token JWT lato server. Pre-feature, l'unica
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# Fixtures


@pytest.fixture
def mem_backend():
    """KeyValueStore in-memory come backend della blacklist (Fase 4: il
    backend e' il KeyValueStore su PG; il fake in-process ha la stessa
    semantica, zero rete)."""
    from app.shared.kv import InMemoryKeyValueStore
    return InMemoryKeyValueStore()


@pytest.fixture
def patched_blacklist(mem_backend):
    """Inietta il backend in-memory nel modulo blacklist; reset dopo."""
    from app.core.security import token_blacklist as tb
    tb._set_client_for_tests(mem_backend)
    yield tb
    tb._reset_client_for_tests()


@pytest.fixture
def auth_client(monkeypatch, patched_blacklist):
    """TestClient con router auth montato + user fittizio, ma senza DB reale.

    Mock di `get_user` in modo che un qualunque sub valido restituisca
    un user attivo — cosi' possiamo testare il flusso auth puro senza
    boot del DB.

    BE-GO-READY-AUTH-ENGINE-015-DEPENDENCIES-OFF-DB-SERVICE: il
    patch path e' migrato dalla facade `auth_mod.db` al canonical
    leaf `app.db.config.users`. Pre-015 `dependencies.get_current_user`
    chiamava `db.get_user(...)` via l'alias facade
    `import app.db.db_service as db`; post-015 chiama
    `_users_dal.get_user(...)` via `import app.db.config.users
    as _users_dal`. Patchando il leaf canonico (l'unico module
    object referenziato da entrambi i siti — auth.py per la
    login e dependencies.py per il get_current_user — via la
    re-export chain `db_service.get_user is config_db_service
    .get_user is config.users.get_user`) il monkeypatch
    continua a coprire entrambi i percorsi senza dover
    duplicare il fake.
    """
    from app.core.auth import auth as auth_mod
    from app.core.auth import db as _users_dal
    from app.core.auth.db import UserRow

    def _fake_get_user(user_id: str):
        return UserRow(
            id=user_id,
            username="testuser",
            group_id="G1",
            role="user",
            is_active=True,
            email=None,
            created_at="2026-01-01T00:00:00Z",
        )

    # AUTH-ENGINE-015: due call site distinti consumano `get_user`
    # in questa flow, e `from X import Y` bind alla import-time
    # rendono i due namespace indipendenti (rebinding su
    # `db_service.get_user` NON propaga a `config.users.get_user`
    # e viceversa):
    #   1) `dependencies.get_current_user` post-015 chiama
    #      `_users_dal.get_user` via `import app.db.config.users
    #      as _users_dal` → patch su `_users_dal.get_user`;
    #   2) `auth.py` route handlers (es. `/auth/me`, `/auth/change-
    #      password`) chiamano `db.get_user` via `import
    #      app.db.db_service as db` → patch su `auth_mod.db.get_user`.
    # Patchiamo entrambe per coprire l'intera flow login + protected.
    monkeypatch.setattr(_users_dal, "get_user", _fake_get_user)
    monkeypatch.setattr(auth_mod.db, "get_user", _fake_get_user)

    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api")

    with TestClient(app, client=("127.0.0.1", 50000)) as tc:
        yield tc, auth_mod


def _issue_token(auth_mod, **overrides) -> str:
    """Emetti un token reale via `create_access_token`. Eventuali
    overrides sul payload vanno passati come kwargs."""
    user_id = overrides.get("user_id", "u1")
    username = overrides.get("username", "testuser")
    group_id = overrides.get("group_id", "G1")
    role = overrides.get("role", "user")
    return auth_mod.create_access_token(user_id, username, group_id, role)


# 1-2. jti claim esiste e univoco


def test_token_contains_jti_claim(auth_client):
    _, auth_mod = auth_client
    tok = _issue_token(auth_mod)
    payload = auth_mod.decode_token(tok)
    assert payload is not None
    assert "jti" in payload
    assert isinstance(payload["jti"], str)
    assert len(payload["jti"]) == 32  # uuid4.hex


def test_two_tokens_have_distinct_jti(auth_client):
    _, auth_mod = auth_client
    t1 = _issue_token(auth_mod)
    t2 = _issue_token(auth_mod)
    p1 = auth_mod.decode_token(t1)
    p2 = auth_mod.decode_token(t2)
    assert p1["jti"] != p2["jti"]


# 3. Endpoint protetto accetta token NON blacklistato


def test_protected_endpoint_ok_when_jti_not_blacklisted(auth_client):
    tc, auth_mod = auth_client
    tok = _issue_token(auth_mod)
    r = tc.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["username"] == "testuser"


# 4. Logout → jti blacklistato → 401 sul prossimo uso


def test_logout_blacklists_jti_and_next_request_401(auth_client, mem_backend):
    tc, auth_mod = auth_client
    tok = _issue_token(auth_mod)
    payload = auth_mod.decode_token(tok)
    jti = payload["jti"]

    # Prima del logout → 200
    r_ok = tc.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert r_ok.status_code == 200

    # Logout
    r_logout = tc.post("/api/auth/logout", headers={"Authorization": f"Bearer {tok}"})
    assert r_logout.status_code == 200
    body = r_logout.json()
    assert body["ok"] is True
    assert body["blacklisted"] is True
    assert body["ttl"] > 0

    # Chiave presente nel backend KV
    assert mem_backend.exists(f"plenora:jwt_blacklist:{jti}") is True

    # Dopo il logout → 401 su endpoint protetto.
    # Post BE-GO-READY-AUTH-DOMAIN-PROMOTION-001: HTTPException emette
    # detail come dict structured `{error, message, details}`. Il
    # messaggio italiano "Token revocato" resta preservato in `message`.
    r_after = tc.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert r_after.status_code == 401
    detail_after = r_after.json()["detail"]
    assert detail_after["error"] == "auth_token_revoked"
    assert "revocato" in detail_after["message"].lower()


# 5. Logout idempotente


def test_logout_idempotent(auth_client):
    tc, auth_mod = auth_client
    tok = _issue_token(auth_mod)

    r1 = tc.post("/api/auth/logout", headers={"Authorization": f"Bearer {tok}"})
    r2 = tc.post("/api/auth/logout", headers={"Authorization": f"Bearer {tok}"})
    # Prima chiamata: blacklist va a buon fine
    assert r1.status_code == 200
    # Seconda chiamata: ora get_current_user check non e' coinvolto
    # (logout non dipende da current_user), ma il jti e' gia' dentro.
    # Il risultato deve restare 200 (SETEX e' idempotente).
    assert r2.status_code == 200
    assert r2.json()["blacklisted"] is True


# 6. Logout senza bearer → 401


def test_logout_without_bearer_returns_401(auth_client):
    tc, _ = auth_client
    r = tc.post("/api/auth/logout")
    assert r.status_code == 401


# 7. Logout con token invalido/expired → 401


def test_logout_with_invalid_token_returns_401(auth_client):
    tc, _ = auth_client
    r = tc.post(
        "/api/auth/logout",
        headers={"Authorization": "Bearer this-is-not-a-jwt"},
    )
    assert r.status_code == 401


def test_logout_with_expired_token_returns_401(auth_client, monkeypatch):
    tc, auth_mod = auth_client
    # Forzo expire negativo
    import jwt as pyjwt
    payload = {
        "sub": "u1",
        "username": "testuser",
        "group_id": "G1",
        "role": "user",
        "exp": datetime.now(timezone.utc) - timedelta(minutes=5),
        "iat": datetime.now(timezone.utc) - timedelta(hours=1),
        "jti": "deadbeef" * 4,
    }
    expired_tok = pyjwt.encode(payload, auth_mod.SECRET_KEY, algorithm=auth_mod.ALGORITHM)
    r = tc.post("/api/auth/logout", headers={"Authorization": f"Bearer {expired_tok}"})
    assert r.status_code == 401


# 8. Logout con token legacy (senza jti) → 200 ma blacklisted=False


def test_logout_legacy_token_without_jti(auth_client):
    tc, auth_mod = auth_client
    # Emetto manualmente un token SENZA claim jti (simulo pre-rollout)
    import jwt as pyjwt
    payload = {
        "sub": "u1",
        "username": "testuser",
        "group_id": "G1",
        "role": "user",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "iat": datetime.now(timezone.utc),
    }
    legacy_tok = pyjwt.encode(payload, auth_mod.SECRET_KEY, algorithm=auth_mod.ALGORITHM)

    r = tc.post("/api/auth/logout", headers={"Authorization": f"Bearer {legacy_tok}"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["blacklisted"] is False


# 9. Logout con Redis giu' → 503 (fail-loud)


def test_logout_redis_down_returns_503(auth_client):
    tc, auth_mod = auth_client
    # Simulo Redis down rimpiazzando il client con None
    from app.core.security import token_blacklist as tb
    tb._set_client_for_tests(None)

    tok = _issue_token(auth_mod)
    r = tc.post("/api/auth/logout", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 503
    # Post BE-GO-READY-AUTH-DOMAIN-PROMOTION-001: detail e' dict structured
    # con error=auth_revocation_unavailable.
    detail = r.json()["detail"]
    assert detail["error"] == "auth_revocation_unavailable"
    assert "revoca" in detail["message"].lower()


# 10. Blacklist check fail-open su Redis down


def test_blacklist_check_fail_open_on_redis_down(auth_client):
    """Token valido + Redis giu' → 200 (non blocca). JWT.exp resta enforced."""
    tc, auth_mod = auth_client
    tok = _issue_token(auth_mod)

    from app.core.security import token_blacklist as tb
    tb._set_client_for_tests(None)

    r = tc.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200


# 11. TTL della chiave ≈ exp - now


def test_blacklist_ttl_matches_remaining_token_lifetime(auth_client):
    tc, auth_mod = auth_client
    from app.core.security import token_blacklist as tb
    from app.shared.kv import InMemoryKeyValueStore

    # Backend che registra il ttl_seconds passato a set() dal blacklist_jti.
    backend_ttls: list = []

    class _RecordingBackend(InMemoryKeyValueStore):
        def set(self, key, value, *, ttl_seconds=None):
            backend_ttls.append(ttl_seconds)
            super().set(key, value, ttl_seconds=ttl_seconds)

    tb._set_client_for_tests(_RecordingBackend())

    tok = _issue_token(auth_mod)
    tc.post("/api/auth/logout", headers={"Authorization": f"Bearer {tok}"})

    # Il backend (PG in prod) riceve ttl = exp - now ~= ACCESS_TOKEN_EXPIRE_HOURS*3600.
    expected = auth_mod.ACCESS_TOKEN_EXPIRE_HOURS * 3600
    stored = [t for t in backend_ttls if t is not None]
    assert stored, "blacklist_jti non ha scritto sul backend con TTL"
    assert expected - 5 <= stored[0] <= expected


# 12. Blacklist di un jti NON impatta altri jti


def test_blacklist_is_per_jti(auth_client):
    tc, auth_mod = auth_client
    tok_a = _issue_token(auth_mod)
    tok_b = _issue_token(auth_mod)

    # Blacklist solo A
    r = tc.post("/api/auth/logout", headers={"Authorization": f"Bearer {tok_a}"})
    assert r.status_code == 200

    # A → 401
    r_a = tc.get("/api/auth/me", headers={"Authorization": f"Bearer {tok_a}"})
    assert r_a.status_code == 401

    # B → 200 (bucket indipendente)
    r_b = tc.get("/api/auth/me", headers={"Authorization": f"Bearer {tok_b}"})
    assert r_b.status_code == 200


# 13. blacklist_jti util: ttl<=0 o jti vuoto → no-op


def test_blacklist_jti_noop_on_invalid_input(patched_blacklist, mem_backend):
    tb = patched_blacklist
    assert tb.blacklist_jti("", 100) is False
    assert tb.blacklist_jti("abc", 0) is False
    assert tb.blacklist_jti("abc", -5) is False
    # Nulla deve essere scritto sul backend
    assert mem_backend.scan_keys("plenora:jwt_blacklist:") == []


def test_is_jti_blacklisted_noop_on_empty_jti(patched_blacklist):
    tb = patched_blacklist
    assert tb.is_jti_blacklisted(None) is False
    assert tb.is_jti_blacklisted("") is False


# METRICHE + HEALTH PROBE


def test_metrics_increment_on_write_ok(patched_blacklist):
    tb = patched_blacklist
    tb._reset_metrics_for_tests()
    assert tb.blacklist_jti("abc", 60) is True
    m = tb.get_metrics()
    assert m["counters"]["write_ok"] == 1
    assert m["counters"]["write_fail"] == 0
    assert m["last_ok_epoch"] is not None


def test_metrics_increment_on_write_fail_redis_down(patched_blacklist):
    tb = patched_blacklist
    tb._reset_metrics_for_tests()
    tb._set_client_for_tests(None)  # simulate Redis down
    assert tb.blacklist_jti("abc", 60) is False
    m = tb.get_metrics()
    assert m["counters"]["write_fail"] == 1
    assert m["counters"]["client_missing"] == 1
    assert m["last_ok_epoch"] is None


def test_metrics_increment_on_read_ok(patched_blacklist, mem_backend):
    tb = patched_blacklist
    # Scrive sul backend bypassando la cache: il read e' un cache miss che
    # colpisce il backend -> read_ok (un cache hit NON conta come read backend).
    mem_backend.set("plenora:jwt_blacklist:abc", "1", ttl_seconds=60)
    tb._reset_metrics_for_tests()
    assert tb.is_jti_blacklisted("abc") is True
    m = tb.get_metrics()
    assert m["counters"]["read_ok"] == 1
    assert m["last_ok_epoch"] is not None


def test_metrics_increment_on_read_fail_redis_down(patched_blacklist):
    tb = patched_blacklist
    tb._reset_metrics_for_tests()
    tb._set_client_for_tests(None)
    assert tb.is_jti_blacklisted("abc") is False
    m = tb.get_metrics()
    assert m["counters"]["read_fail"] == 1
    assert m["counters"]["client_missing"] == 1


def test_get_blacklist_status_ok_with_pg_backend(patched_blacklist):
    tb = patched_blacklist
    tb._reset_metrics_for_tests()
    tb.blacklist_jti("abc", 60)  # un'op reale → aggiorna last_ok
    status = tb.get_blacklist_status()
    assert status["status"] == "ok"
    assert "operativa" in status["message"].lower()
    assert status["minutes_since_last_ok"] is not None
    assert status["counters"]["write_ok"] == 1


def test_get_blacklist_status_unknown_when_redis_missing(patched_blacklist):
    tb = patched_blacklist
    tb._reset_metrics_for_tests()
    tb._set_client_for_tests(None)
    status = tb.get_blacklist_status()
    assert status["status"] == "unknown"
    assert status["minutes_since_last_ok"] is None


def test_get_blacklist_status_warn_when_stale(patched_blacklist, monkeypatch):
    """Se nessuna op reale e' riuscita da oltre la soglia, status=warn."""
    tb = patched_blacklist
    tb._reset_metrics_for_tests()
    # Simulo last_ok molto nel passato (1h fa)
    import time
    tb._last_ok_epoch = time.time() - 3600
    # Lo store in-memory risponde alle letture → non e' error
    status = tb.get_blacklist_status(stale_threshold_seconds=1800)
    assert status["status"] == "warn"
    assert "inoperante" in status["message"].lower() or "nessuna operazione" in status["message"].lower()
    assert status["minutes_since_last_ok"] >= 59


def test_get_blacklist_status_error_when_exists_raises(patched_blacklist):
    """Redis risponde ma EXISTS solleva → status=error."""
    tb = patched_blacklist
    tb._reset_metrics_for_tests()

    class BrokenClient:
        def exists(self, *a, **kw):
            raise RuntimeError("simulated redis error")

    tb._set_client_for_tests(BrokenClient())
    status = tb.get_blacklist_status()
    assert status["status"] == "error"
    assert "simulated" in status["error"]


def test_healthcheck_does_not_create_key(patched_blacklist, mem_backend):
    """Il probe exists non deve creare la chiave healthcheck nel backend."""
    tb = patched_blacklist
    tb.get_blacklist_status()
    assert mem_backend.exists("plenora:jwt_blacklist:healthcheck") is False


# Lazy-init del backend (Plenora 2.0 Fase 4: PostgreSQL, non piu' Redis)


def test_get_client_lazy_inits_pg_backend(patched_blacklist):
    """Senza iniezione esplicita, _get_client ritorna un PgKeyValueStore."""
    from app.shared.kv import PgKeyValueStore

    tb = patched_blacklist
    tb._reset_client_for_tests()  # torna allo stato lazy-init
    client = tb._get_client()
    assert isinstance(client, PgKeyValueStore)
