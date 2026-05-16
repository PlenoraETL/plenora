"""
Auth in-memory rate-limit fallback — typed boundary.

Used only when `LOGIN_RL_REDIS_REQUIRED` is false or Redis is not enabled in
the shared login limiter. Cluster deployments should enable Redis-required
mode so this store remains a development/single-node compatibility path.

"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import MutableMapping
from dataclasses import dataclass, field


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
        failed_attempts: per-IP list of `time.monotonic`
            timestamps for failed login attempts within (or recently
            within) the sliding window. Pruned on every `check`.

    The dataclass is mutable on purpose: the singleton `_STORE` is
    shared by all callers and aliased in `auth.py` for compat. Tests
    mutate `failed_attempts` directly via the alias.
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    failed_attempts: MutableMapping[str, list[float]] = field(
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
# `auth.py` for test compatibility
_STORE = InMemoryRateLimitFallback()


# Module-level functional wrappers
# Useful when callers want a plain callable (e.g
# `login_rate_limiter.check_rate_limit(..., fallback=<callable>)`
# `record_failed_login_in_memory` and `clear_failed_logins_in_memory`
# match the (ip: str) -> None signature expected by the limiter
# `check_rate_limit_in_memory` requires thresholds: callers compose
# a thin closure (or use the auth.py wrapper that already does


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
