"""
Endpoint contract test — rate-limit login isolato per-IP dietro proxy trusted.

Blinda il fix del reperto #27: prima di get_client_ip, TUTTI i login
"""

from __future__ import annotations


import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


@pytest.fixture
def client_with_auth_router(monkeypatch):
    """Costruisce TestClient con solo il router auth montato.

    Mock delle dipendenze esterne:
      - db.get_user_by_username → None (utente non esiste, trigger
        fallback con _DUMMY_PASSWORD_HASH)
      - verify_password → False (evita bcrypt ~200ms per request)
    Reset dello store in-memory canonical per isolation tra test.
    """
    # Reset rate-limit state prima di ogni test
    from app.core.auth import auth as auth_mod
    from app.core.auth import rate_limit_fallback as fallback_rl
    with fallback_rl._STORE.lock:
        fallback_rl._STORE.failed_attempts.clear()

    # R-01: il limiter ora prova Redis prima del fallback in-memory.
    # Questo test esercita il path in-memory (resetta lo store fallback),
    # quindi forziamo Redis client a None cosi' il wrapper cade sempre
    # sul fallback indipendentemente da un Redis attivo sull'host di test.
    from app.core.security import login_rate_limiter as _rl_mod
    _rl_mod._set_client_for_tests(None)
    _rl_mod._reset_state_for_tests()

    # Mock dipendenze che toccano DB/bcrypt
    monkeypatch.setattr(auth_mod.db, "get_user_by_username", lambda _: None)
    monkeypatch.setattr(auth_mod, "verify_password", lambda *a, **kw: False)

    # TRUSTED_PROXIES deve includere 127.0.0.1 (peer tipico TestClient)
    monkeypatch.setenv("TRUSTED_PROXIES", "127.0.0.1,::1")

    # LOGIN_MAX_ATTEMPTS dev'essere un valore noto per il test.
    # NB: _check_rate_limit legge LOGIN_MAX_ATTEMPTS come modulo-level
    # constant calcolato all'import. Lo patchamo direttamente.
    monkeypatch.setattr(auth_mod, "LOGIN_MAX_ATTEMPTS", 5)

    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api")

    # Force peer = 127.0.0.1: di default TestClient usa "testclient"
    # (stringa), che non matcha nessun CIDR/IP nella trusted list,
    # rendendo il peer non-trusted e XFF ignorato — esattamente il
    # comportamento Previous behavior che stiamo cercando di validare contro.
    with TestClient(app, client=("127.0.0.1", 50000)) as tc:
        yield tc

    # Cleanup dopo
    with fallback_rl._STORE.lock:
        fallback_rl._STORE.failed_attempts.clear()
    _rl_mod._reset_client_for_tests()


def _login_fail(client: TestClient, xff_ip: str):
    """Invia un POST /api/auth/login con credenziali sicuramente wrong.
    Ritorna la response."""
    return client.post(
        "/api/auth/login",
        json={"username": "nobody", "password": "wrong"},
        headers={"X-Forwarded-For": xff_ip},
    )


# Test: rate-limit isolato per-IP


def test_login_rate_limit_triggers_after_max_attempts(client_with_auth_router):
    """Dopo LOGIN_MAX_ATTEMPTS failed login dallo stesso IP XFF,
    l'attempt successivo riceve 429."""
    # 5 failed (tutti 401, user non esiste)
    for i in range(5):
        r = _login_fail(client_with_auth_router, "1.2.3.4")
        assert r.status_code == 401, f"attempt #{i+1}: atteso 401, got {r.status_code}"

    # Il 6° → rate-limited (429)
    r = _login_fail(client_with_auth_router, "1.2.3.4")
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_login_rate_limit_is_isolated_per_xff_ip(client_with_auth_router):
    """Rate-limit per-IP: se IP A esaurisce il bucket, IP B e' ancora
    libero. Senza il fix trusted-proxy, entrambi condividerebbero
    127.0.0.1 e B verrebbe bloccato dagli attempt di A.
    """
    # IP "A" = 10.0.0.1 esaurisce il bucket (5 fail + 1 rate-limited)
    for _ in range(5):
        r = _login_fail(client_with_auth_router, "10.0.0.1")
        assert r.status_code == 401
    r = _login_fail(client_with_auth_router, "10.0.0.1")
    assert r.status_code == 429  # A e' bloccato

    # IP "B" = 10.0.0.2 deve essere ancora libero: il suo bucket e' vuoto,
    # dovrebbe ricevere 401 (credenziali wrong), NON 429
    r = _login_fail(client_with_auth_router, "10.0.0.2")
    assert r.status_code == 401, (
        "IP 10.0.0.2 non dovrebbe essere rate-limited dagli attempts di "
        "10.0.0.1 — se questo fallisce, il bucket e' condiviso (Previous behavior)"
    )


def test_login_untrusted_peer_xff_ignored_shared_bucket(client_with_auth_router, monkeypatch):
    """Regressione diretta del reperto: se il peer NON e' trusted, XFF
    viene ignorato. Tutti gli attempts, qualunque XFF, finiscono nello
    stesso bucket (peer-IP).

    Forziamo peer non-trusted resettando TRUSTED_PROXIES a qualcosa che
    NON contiene 127.0.0.1 (il TestClient).
    """
    # Reset state PER QUESTO test
    from app.core.auth import rate_limit_fallback as fallback_rl
    with fallback_rl._STORE.lock:
        fallback_rl._STORE.failed_attempts.clear()
    # R-01: vedi sopra — forziamo il fallback in-memory.
    from app.core.security import login_rate_limiter as _rl_mod
    _rl_mod._set_client_for_tests(None)

    # 127.0.0.1 non e' piu' trusted → XFF ignorato → tutti condividono
    # il bucket di 127.0.0.1
    monkeypatch.setenv("TRUSTED_PROXIES", "10.99.99.99")

    # 5 fail con XFF "A"
    for _ in range(5):
        r = _login_fail(client_with_auth_router, "1.1.1.1")
        assert r.status_code == 401

    # Un tentativo con XFF "B": dovrebbe essere 429 perche' il bucket
    # 127.0.0.1 e' esaurito (XFF ignorato). Conferma: senza peer trusted,
    # XFF non protegge.
    r = _login_fail(client_with_auth_router, "2.2.2.2")
    assert r.status_code == 429
