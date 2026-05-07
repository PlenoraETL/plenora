"""
Contract test BE-GO-READY-AUTH-ENGINE-002 — in-memory rate-limit

fallback estratto da `auth.py` in
"""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# Fixture: store fresco per ogni test (no shared state cross-test)


@pytest.fixture
def fresh_store():
    """Reset dello store singleton prima e dopo ogni test."""
    from app.core.auth import rate_limit_fallback as rl

    with rl._STORE.lock:
        rl._STORE.failed_attempts.clear()
    yield rl._STORE
    with rl._STORE.lock:
        rl._STORE.failed_attempts.clear()


# 1. Threshold + raise shape


def test_check_under_threshold_does_not_raise(fresh_store):
    from app.core.auth import rate_limit_fallback as rl

    # 3 record < max_attempts=5 ⇒ check non solleva.
    for _ in range(3):
        rl.record_failed_login_in_memory("1.2.3.4")
    rl.check_rate_limit_in_memory(
        "1.2.3.4",
        max_attempts=5,
        window_seconds=60,
        lockout_seconds=300,
    )


def test_check_at_threshold_raises_triggered(fresh_store):
    from app.core.auth import rate_limit_fallback as rl

    # 5 record ⇒ check al 6° fail (>= threshold) solleva.
    for _ in range(5):
        rl.record_failed_login_in_memory("1.2.3.4")
    with pytest.raises(rl.RateLimitFallbackTriggered) as ei:
        rl.check_rate_limit_in_memory(
            "1.2.3.4",
            max_attempts=5,
            window_seconds=60,
            lockout_seconds=300,
        )
    # retry_after non-clampato (clamp lo fa disclosure.rate_limit_exceeded).
    assert isinstance(ei.value.retry_after, int)
    # Limite teorico: 0 < retry_after <= lockout_seconds (300).
    assert 0 < ei.value.retry_after <= 300


def test_triggered_carries_retry_after_attribute():
    from app.core.auth.rate_limit_fallback import RateLimitFallbackTriggered

    e = RateLimitFallbackTriggered(42)
    assert e.retry_after == 42
    # Coercion int (anche da float negativi → -7 invariato; clamp sta nel caller)
    e2 = RateLimitFallbackTriggered(-7)
    assert e2.retry_after == -7


# 2. Sliding window pruning


def test_check_prunes_timestamps_older_than_window(fresh_store, monkeypatch):
    """Timestamp scaduti vengono rimossi prima del count-check."""
    from app.core.auth import rate_limit_fallback as rl

    # Inietta 5 timestamp "vecchi" oltre il window.
    fake_old = 1000.0
    fake_now = 1000.0 + 120.0  # 2 minuti dopo
    fresh_store.failed_attempts["1.2.3.4"] = [fake_old] * 5

    # window=60 ⇒ tutti pruned. check non solleva nemmeno con
    # threshold=1 perche' la lista viene svuotata prima del count.
    monkeypatch.setattr(rl.time, "monotonic", lambda: fake_now)
    rl.check_rate_limit_in_memory(
        "1.2.3.4",
        max_attempts=1,
        window_seconds=60,
        lockout_seconds=300,
    )
    # Lista pruned in-place
    assert fresh_store.failed_attempts["1.2.3.4"] == []


# 3. Per-IP isolation


def test_record_isolates_ip_buckets(fresh_store):
    from app.core.auth import rate_limit_fallback as rl

    rl.record_failed_login_in_memory("A")
    rl.record_failed_login_in_memory("A")
    rl.record_failed_login_in_memory("B")
    assert len(fresh_store.failed_attempts["A"]) == 2
    assert len(fresh_store.failed_attempts["B"]) == 1


def test_clear_isolates_ip_buckets(fresh_store):
    from app.core.auth import rate_limit_fallback as rl

    rl.record_failed_login_in_memory("A")
    rl.record_failed_login_in_memory("B")
    rl.clear_failed_logins_in_memory("A")
    assert "A" not in fresh_store.failed_attempts
    assert len(fresh_store.failed_attempts["B"]) == 1


def test_clear_unknown_ip_is_idempotent(fresh_store):
    from app.core.auth import rate_limit_fallback as rl

    # Nessuna eccezione su clear di IP non esistente.
    rl.clear_failed_logins_in_memory("never-seen-before")


# 4. Lock identity (concorrenza minimal smoke)


def test_store_lock_is_a_threading_lock(fresh_store):
    """Il lock deve essere un threading.Lock acquisibile (tipi-anatra ok)."""
    from app.core.auth import rate_limit_fallback as rl

    # threading.Lock e' una factory che ritorna `_thread.lock`. Test
    # comportamentale: acquisibile / rilasciabile / context manager.
    with rl._STORE.lock:
        pass
    assert rl._STORE.lock.acquire(blocking=False)
    rl._STORE.lock.release()


# 5. KEEP_COMPAT alias in auth.py


def test_auth_module_aliases_point_to_extracted_store(fresh_store):
    """`auth_mod._rate_lock` e `auth_mod._failed_attempts` devono
    essere lo *stesso oggetto* del nuovo store, cosi' i test esistenti
    che fanno `auth_mod._failed_attempts.clear()` svuotano realmente
    il bucket usato dal fallback."""
    from app.core.auth import auth as auth_mod
    from app.core.auth import rate_limit_fallback as rl

    assert auth_mod._rate_lock is rl._STORE.lock
    assert auth_mod._failed_attempts is rl._STORE.failed_attempts


def test_auth_module_record_clear_aliases_are_module_functions():
    """Le 2 funzioni-callback (record/clear) usate da
    `login_rate_limiter` come `fallback=` sono esportate via auth.py
    e provengono dal modulo nuovo. Identita' preservata permette i
    monkeypatch esistenti su `auth_mod._record_failed_login_in_memory`."""
    from app.core.auth import auth as auth_mod
    from app.core.auth import rate_limit_fallback as rl

    assert auth_mod._record_failed_login_in_memory is rl.record_failed_login_in_memory
    assert auth_mod._clear_failed_logins_in_memory is rl.clear_failed_logins_in_memory


def test_auth_check_wrapper_converts_triggered_to_disclosure(fresh_store):
    """Il wrapper KEEP_COMPAT in `auth.py` cattura
    `RateLimitFallbackTriggered` e raise via
    `disclosure.raise_disclosure(disclosure.rate_limit_exceeded(...))`.
    Il risultato visibile al caller e' una `HTTPException(429)` con
    `Retry-After` >= 1 (clamp eseguito da disclosure)."""
    from fastapi import HTTPException

    from app.core.auth import auth as auth_mod
    from app.shared import error_codes

    # 5 record + monkeypatch threshold a 5 ⇒ check al 6° solleva.
    for _ in range(5):
        auth_mod._record_failed_login_in_memory("1.1.1.1")

    with patch.object(auth_mod, "LOGIN_MAX_ATTEMPTS", 5), \
         patch.object(auth_mod, "LOGIN_WINDOW_SECONDS", 60), \
         patch.object(auth_mod, "LOGIN_LOCKOUT_SECONDS", 300):
        with pytest.raises(HTTPException) as ei:
            auth_mod._check_rate_limit_in_memory("1.1.1.1")

    assert ei.value.status_code == 429
    assert ei.value.detail["error"] == error_codes.AUTH_RATE_LIMIT_EXCEEDED
    headers = dict(ei.value.headers or {})
    assert "Retry-After" in headers
    assert int(headers["Retry-After"]) >= 1


def test_auth_check_wrapper_reads_monkeypatched_constants(fresh_store):
    """I 3 thresholds devono essere letti da `auth.py` ad ogni
    chiamata (NON cached) cosi' i test esistenti che fanno
    `monkeypatch.setattr(auth_mod, "LOGIN_MAX_ATTEMPTS", 2)`
    funzionano ancora."""
    from fastapi import HTTPException

    from app.core.auth import auth as auth_mod

    # Threshold ridotto a 2 via patch ⇒ il 3° check raise.
    auth_mod._record_failed_login_in_memory("9.9.9.9")
    auth_mod._record_failed_login_in_memory("9.9.9.9")
    with patch.object(auth_mod, "LOGIN_MAX_ATTEMPTS", 2), \
         patch.object(auth_mod, "LOGIN_WINDOW_SECONDS", 60), \
         patch.object(auth_mod, "LOGIN_LOCKOUT_SECONDS", 300):
        with pytest.raises(HTTPException) as ei:
            auth_mod._check_rate_limit_in_memory("9.9.9.9")
    assert ei.value.status_code == 429


# 6. Smoke concorrenza: record da 5 thread non perde timestamp


def test_concurrent_record_does_not_lose_timestamps(fresh_store):
    """Sanity check: 5 thread che fanno 20 record/cad sullo stesso IP
    devono produrre 100 timestamp sotto la stessa chiave (lock)."""
    from app.core.auth import rate_limit_fallback as rl

    def _hammer():
        for _ in range(20):
            rl.record_failed_login_in_memory("smoke")

    threads = [threading.Thread(target=_hammer) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(fresh_store.failed_attempts["smoke"]) == 100
