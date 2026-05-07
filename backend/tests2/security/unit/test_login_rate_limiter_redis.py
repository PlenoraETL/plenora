"""Contract test — login rate limiter Redis-backed (R-01).

Copre il modulo `app.shared.login_rate_limiter`:

  1. limit applicato per IP (sliding window) usando un fakeredis;
  2. window/reset: dopo che il window scade, il bucket si svuota;
  3. cluster-safety simulata: due "istanze" che condividono lo
     stesso fakeredis store vedono lo stesso bucket;
  4. Redis unavailable: il limiter delega al `fallback` callable
     (in single-node = parita' col vecchio limiter in-memory).

I test usano `time.time()` reale per pochi secondi: settiamo window
piccoli (2-3s) cosi' il wall-clock e' lecito senza freezegun. Soglie
generose (1.5s) per evitare flake.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import fakeredis
import pytest
from fastapi import HTTPException


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# Fixtures


@pytest.fixture
def fake_client():
    """Un client fakeredis condiviso a livello di test."""
    return fakeredis.FakeStrictRedis(decode_responses=True)


@pytest.fixture
def rl_with_redis(fake_client):
    """Inietta `fake_client` nel limiter; cleanup dopo."""
    from app.shared import login_rate_limiter as rl

    rl._set_client_for_tests(fake_client)
    rl._reset_state_for_tests()
    yield rl
    rl._reset_client_for_tests()
    rl._reset_state_for_tests()


@pytest.fixture
def rl_no_redis():
    """Forza Redis a None: il limiter cade sul fallback."""
    from app.shared import login_rate_limiter as rl

    rl._set_client_for_tests(None)
    rl._reset_state_for_tests()
    yield rl
    rl._reset_client_for_tests()
    rl._reset_state_for_tests()


def _no_op_fallback(_ip: str) -> None:
    """Fallback che non fa nulla: usato quando il test verifica il path Redis."""
    pass


# 1. Limit applicato per IP via Redis


def test_redis_limit_triggers_after_max_attempts(rl_with_redis):
    """Dopo MAX tentativi falliti, il check successivo solleva 429."""
    rl = rl_with_redis
    ip = "10.0.0.1"
    MAX = 3
    WINDOW = 60
    LOCKOUT = 300

    # MAX-1 record: nessun blocco
    for _ in range(MAX - 1):
        rl.record_failed_login(ip, window_seconds=WINDOW, fallback=_no_op_fallback)
        rl.check_rate_limit(
            ip,
            max_attempts=MAX,
            window_seconds=WINDOW,
            lockout_seconds=LOCKOUT,
            fallback=_no_op_fallback,
        )

    # MAX-esimo record + check → 429
    rl.record_failed_login(ip, window_seconds=WINDOW, fallback=_no_op_fallback)
    with pytest.raises(HTTPException) as exc:
        rl.check_rate_limit(
            ip,
            max_attempts=MAX,
            window_seconds=WINDOW,
            lockout_seconds=LOCKOUT,
            fallback=_no_op_fallback,
        )
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers
    # retry_after = lockout - (now - oldest), con oldest very recent →
    # quindi vicino a LOCKOUT, comunque >=1.
    assert int(exc.value.headers["Retry-After"]) >= 1


def test_redis_limit_isolated_per_ip(rl_with_redis):
    """Bucket separati per IP: A esaurisce, B resta libero."""
    rl = rl_with_redis
    MAX = 2
    WINDOW = 60
    LOCKOUT = 300

    for _ in range(MAX):
        rl.record_failed_login("ip-A", window_seconds=WINDOW, fallback=_no_op_fallback)
    with pytest.raises(HTTPException):
        rl.check_rate_limit(
            "ip-A",
            max_attempts=MAX,
            window_seconds=WINDOW,
            lockout_seconds=LOCKOUT,
            fallback=_no_op_fallback,
        )

    # B: nessun record → check passa
    rl.check_rate_limit(
        "ip-B",
        max_attempts=MAX,
        window_seconds=WINDOW,
        lockout_seconds=LOCKOUT,
        fallback=_no_op_fallback,
    )


def test_redis_clear_resets_bucket(rl_with_redis):
    """`clear_failed_logins` rimuove il bucket: dopo il login OK l'IP riparte."""
    rl = rl_with_redis
    MAX = 2
    WINDOW = 60
    LOCKOUT = 300
    ip = "10.0.0.42"

    for _ in range(MAX):
        rl.record_failed_login(ip, window_seconds=WINDOW, fallback=_no_op_fallback)
    with pytest.raises(HTTPException):
        rl.check_rate_limit(
            ip, max_attempts=MAX, window_seconds=WINDOW,
            lockout_seconds=LOCKOUT, fallback=_no_op_fallback,
        )

    rl.clear_failed_logins(ip, fallback=_no_op_fallback)

    # Bucket vuoto: nessun raise
    rl.check_rate_limit(
        ip, max_attempts=MAX, window_seconds=WINDOW,
        lockout_seconds=LOCKOUT, fallback=_no_op_fallback,
    )


# 2. Sliding window — i record vecchi escono dal conteggio


def test_redis_sliding_window_drops_old_attempts(rl_with_redis):
    """Record fuori dal window vengono pruned dal check (ZREMRANGEBYSCORE)."""
    rl = rl_with_redis
    MAX = 3
    WINDOW = 2  # piccolo cosi' il test e' veloce
    LOCKOUT = 300
    ip = "10.0.0.99"

    # Saturiamo il bucket
    for _ in range(MAX):
        rl.record_failed_login(ip, window_seconds=WINDOW, fallback=_no_op_fallback)

    # check immediato → 429
    with pytest.raises(HTTPException):
        rl.check_rate_limit(
            ip, max_attempts=MAX, window_seconds=WINDOW,
            lockout_seconds=LOCKOUT, fallback=_no_op_fallback,
        )

    # Aspetta che il window scada (con margine generoso anti-flake)
    time.sleep(WINDOW + 1.0)

    # Ora il bucket e' vuoto (i 3 record erano oltre 2s fa)
    rl.check_rate_limit(
        ip, max_attempts=MAX, window_seconds=WINDOW,
        lockout_seconds=LOCKOUT, fallback=_no_op_fallback,
    )


# 3. Cluster-safety simulata — due "istanze" condividono lo store


def test_cluster_safety_two_callers_share_bucket(fake_client):
    """Due chiamanti che usano lo stesso fakeredis store condividono il
    bucket: i tentativi del primo influenzano il check del secondo —
    proprio cio' che il vecchio limiter in-memory non garantiva."""
    from app.shared import login_rate_limiter as rl

    rl._set_client_for_tests(fake_client)
    rl._reset_state_for_tests()
    try:
        MAX = 3
        WINDOW = 60
        LOCKOUT = 300
        ip = "10.99.0.1"

        # "Istanza 1": registra MAX tentativi falliti
        for _ in range(MAX):
            rl.record_failed_login(ip, window_seconds=WINDOW, fallback=_no_op_fallback)

        # "Istanza 2" (stesso modulo, stessa connessione fakeredis):
        # check sullo stesso IP deve vedere il bucket pieno → 429.
        with pytest.raises(HTTPException) as exc:
            rl.check_rate_limit(
                ip, max_attempts=MAX, window_seconds=WINDOW,
                lockout_seconds=LOCKOUT, fallback=_no_op_fallback,
            )
        assert exc.value.status_code == 429
    finally:
        rl._reset_client_for_tests()
        rl._reset_state_for_tests()


# 4. Redis unavailable → fallback callable invocato (single-node parity)


def test_fallback_called_when_redis_client_is_none(rl_no_redis):
    """Con Redis None, le 3 API delegano al `fallback` passato."""
    rl = rl_no_redis
    calls: list[tuple[str, str]] = []

    def fb_check(ip):    calls.append(("check", ip))
    def fb_record(ip):   calls.append(("record", ip))
    def fb_clear(ip):    calls.append(("clear", ip))

    rl.check_rate_limit("ip-x", max_attempts=10, window_seconds=60,
                        lockout_seconds=300, fallback=fb_check)
    rl.record_failed_login("ip-x", window_seconds=60, fallback=fb_record)
    rl.clear_failed_logins("ip-x", fallback=fb_clear)

    assert calls == [("check", "ip-x"), ("record", "ip-x"), ("clear", "ip-x")]


def test_fallback_called_when_redis_pipeline_raises():
    """Se Redis e' "online" ma l'I/O solleva (es. timeout, server error),
    cadiamo sul fallback. Simulato con un client che esplode su `pipeline()`.
    """
    from app.shared import login_rate_limiter as rl

    class ExplodingClient:
        def pipeline(self, *_a, **_kw):
            raise ConnectionError("simulated redis down")

        def delete(self, *_a, **_kw):
            raise ConnectionError("simulated redis down")

    rl._set_client_for_tests(ExplodingClient())
    rl._reset_state_for_tests()
    try:
        called: list[str] = []

        def fb(ip):
            called.append(ip)

        rl.check_rate_limit("ip-y", max_attempts=10, window_seconds=60,
                            lockout_seconds=300, fallback=fb)
        rl.record_failed_login("ip-y", window_seconds=60, fallback=fb)
        rl.clear_failed_logins("ip-y", fallback=fb)
        assert called == ["ip-y", "ip-y", "ip-y"]
    finally:
        rl._reset_client_for_tests()
        rl._reset_state_for_tests()


# 5. Parita' del fallback in-memory col vecchio limiter (smoke)


def test_fallback_inmemory_matches_legacy_behaviour(rl_no_redis):
    """Wrapper auth.py + Redis disabilitato + store in-memory legacy:
    saturare `_failed_attempts` produce 429 con la stessa formula di prima.
    """
    from app.core.auth import auth as auth_mod

    # Reset legacy store
    with auth_mod._rate_lock:
        auth_mod._failed_attempts.clear()

    # Patcha le costanti perche' il test sia veloce e prevedibile.
    with patch.object(auth_mod, "LOGIN_MAX_ATTEMPTS", 2), \
         patch.object(auth_mod, "LOGIN_WINDOW_SECONDS", 60), \
         patch.object(auth_mod, "LOGIN_LOCKOUT_SECONDS", 300):

        ip = "10.0.0.7"
        # 2 fallimenti
        auth_mod._record_failed_login(ip)
        auth_mod._record_failed_login(ip)

        # Il 3° check supera la soglia → 429
        with pytest.raises(HTTPException) as exc:
            auth_mod._check_rate_limit(ip)
        assert exc.value.status_code == 429
        assert int(exc.value.headers["Retry-After"]) >= 1

        # clear → bucket vuoto, niente 429
        auth_mod._clear_failed_logins(ip)
        auth_mod._check_rate_limit(ip)
