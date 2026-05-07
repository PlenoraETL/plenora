"""Auth in-memory rate-limit fallback — typed boundary.

BE-GO-READY-AUTH-ENGINE-002-RATE-LIMIT-FALLBACK-EXTRACTION.

Extract from `auth.py` the in-memory sliding-window fallback used by
`app.shared.login_rate_limiter` when Redis is not available
(FAIL-SOFT, single-node). The store and its 3 functions previously
lived as module-level globals in `auth.py`; here they live in a
focused, typed module.

Boundary contract
=================
This module owns the *fallback storage and algorithm only*. It does
NOT know about:

- HTTP disclosure shape (`app.core.auth.disclosure`); the caller
  catches `RateLimitFallbackTriggered` and converts to a
  `DisclosureDecision`.
- Threshold values `LOGIN_MAX_ATTEMPTS` / `LOGIN_WINDOW_SECONDS` /
  `LOGIN_LOCKOUT_SECONDS`. They are passed as keyword args to
  `check_rate_limit(...)` so the caller (auth.py) can preserve the
  module-level monkeypatch surface used by existing tests
  (`tests2/security/unit/test_login_rate_limit_per_ip.py`).
- Redis-backed limiter (`app.shared.login_rate_limiter`).
- Users / MFA / sessions / DB.

Algorithm (preserved 1:1 from the legacy implementation)
========================================================
- Per-IP sliding window of `window_seconds` over `_failed_attempts[ip]`
  (list of `time.monotonic()` timestamps).
- A check that finds `>= max_attempts` timestamps in window raises
  `RateLimitFallbackTriggered(retry_after)` with
  `retry_after = lockout_seconds - (now - oldest_in_window)`. The
  caller (or the `disclosure.rate_limit_exceeded(...)` helper)
  clamps to >=1 — the same byte-for-byte semantics as before.
- A record appends `time.monotonic()` to the per-IP list.
- A clear pops the IP's list entirely.

KEEP_COMPAT surface in auth.py
==============================
`auth.py` keeps these module-level aliases for test compatibility
(`KEEP_COMPAT`):

- `_rate_lock` -> `_STORE.lock`
- `_failed_attempts` -> `_STORE.failed_attempts`
- `_check_rate_limit_in_memory(ip)` -> wrapper that reads thresholds
  from the auth.py module-level constants and delegates to
  `check_rate_limit_in_memory(...)`, then converts
  `RateLimitFallbackTriggered` into the disclosure raise.
- `_record_failed_login_in_memory(ip)` -> alias of
  `record_failed_login_in_memory`.
- `_clear_failed_logins_in_memory(ip)` -> alias of
  `clear_failed_logins_in_memory`.

The aliases preserve test imports/monkeypatch on
`app.core.auth.auth._failed_attempts.clear()`,
`auth_mod._rate_lock`, and the wrapper functions used as `fallback=`
callables for `app.shared.login_rate_limiter`.

Go-ready mapping
================
    type RateLimitFallbackStore struct {
        Lock           sync.Mutex
        FailedAttempts map[string][]float64
    }

    func (s *RateLimitFallbackStore) Check(ip string, opts CheckOpts) error
    func (s *RateLimitFallbackStore) Record(ip string)
    func (s *RateLimitFallbackStore) Clear(ip string)

    type RateLimitFallbackTriggered struct{ RetryAfter int }
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List


__all__ = [
    "RateLimitFallbackTriggered",
    "InMemoryRateLimitFallback",
    "check_rate_limit_in_memory",
    "record_failed_login_in_memory",
    "clear_failed_logins_in_memory",
]


class RateLimitFallbackTriggered(Exception):
    """Raised by `InMemoryRateLimitFallback.check(...)` when the IP
    has reached or exceeded the threshold within the sliding window.

    Carries `retry_after` (raw, unclamped) so the caller can choose
    how to surface it (today: `disclosure.rate_limit_exceeded(...)`
    clamps to >=1 and emits 429 + `Retry-After` header).
    """

    def __init__(self, retry_after: int):
        self.retry_after = int(retry_after)
        super().__init__(f"rate_limit_fallback_triggered: retry_after={self.retry_after}")


@dataclass
class InMemoryRateLimitFallback:
    """Sliding-window rate-limit fallback store.

    Attributes:
        lock: re-entrant-safe lock around `failed_attempts`. Tests
            acquire this directly via the auth.py alias to clear
            state cooperatively.
        failed_attempts: per-IP list of `time.monotonic()`
            timestamps for failed login attempts within (or recently
            within) the sliding window. Pruned on every `check`.

    The dataclass is mutable on purpose: the singleton `_STORE` is
    shared by all callers and aliased in `auth.py` for compat. Tests
    mutate `failed_attempts` directly via the alias.
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    failed_attempts: Dict[str, List[float]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def check(
        self,
        ip: str,
        *,
        max_attempts: int,
        window_seconds: int,
        lockout_seconds: int,
    ) -> None:
        """Sliding-window check — raise `RateLimitFallbackTriggered`
        if the per-IP attempts in window have reached `max_attempts`.

        Side effect: prunes timestamps older than `window_seconds`
        from `failed_attempts[ip]` before the count check (so the
        list does not grow unbounded across long quiet periods).

        Algorithm preserved 1:1 from `auth._check_rate_limit_in_memory`:
        the retry_after value is passed unclamped to the exception;
        the disclosure layer clamps to >=1 (matches
        `tests2/security/unit/test_auth_disclosure_policy_contract.py::
        test_rate_limit_exceeded_clamps_retry_after_to_min_one`).
        """
        now = time.monotonic()
        with self.lock:
            self.failed_attempts[ip] = [
                t for t in self.failed_attempts[ip] if now - t < window_seconds
            ]
            if len(self.failed_attempts[ip]) >= max_attempts:
                oldest = self.failed_attempts[ip][0]
                retry_after = int(lockout_seconds - (now - oldest))
                raise RateLimitFallbackTriggered(retry_after)

    def record(self, ip: str) -> None:
        """Append a `time.monotonic()` timestamp to the per-IP list.
        Called on every failed login (after `check` has cleared the
        attempt to proceed)."""
        with self.lock:
            self.failed_attempts[ip].append(time.monotonic())

    def clear(self, ip: str) -> None:
        """Pop the per-IP list. Called on successful login to reset
        the bucket. Idempotent: safe to call when the IP has no
        history."""
        with self.lock:
            self.failed_attempts.pop(ip, None)


# Module-level singleton: shared across all callers, aliased in
# `auth.py` for test compatibility.
_STORE = InMemoryRateLimitFallback()


# Module-level functional wrappers
# Useful when callers want a plain callable (e.g.
# `login_rate_limiter.check_rate_limit(..., fallback=<callable>)`).
# `record_failed_login_in_memory` and `clear_failed_logins_in_memory`
# match the (ip: str) -> None signature expected by the limiter.
# `check_rate_limit_in_memory` requires thresholds: callers compose
# a thin closure (or use the auth.py wrapper that already does).


def check_rate_limit_in_memory(
    ip: str,
    *,
    max_attempts: int,
    window_seconds: int,
    lockout_seconds: int,
) -> None:
    """Functional wrapper around `_STORE.check`. Raises
    `RateLimitFallbackTriggered` on threshold reached."""
    _STORE.check(
        ip,
        max_attempts=max_attempts,
        window_seconds=window_seconds,
        lockout_seconds=lockout_seconds,
    )


def record_failed_login_in_memory(ip: str) -> None:
    """Functional wrapper around `_STORE.record`. Plain
    `(ip: str) -> None` signature compatible with
    `login_rate_limiter.record_failed_login(..., fallback=...)`."""
    _STORE.record(ip)


def clear_failed_logins_in_memory(ip: str) -> None:
    """Functional wrapper around `_STORE.clear`. Plain
    `(ip: str) -> None` signature compatible with
    `login_rate_limiter.clear_failed_logins(..., fallback=...)`."""
    _STORE.clear(ip)
