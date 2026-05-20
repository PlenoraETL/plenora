"""Rate-limit endpoints.

Tracks counters in process memory keyed by ``(endpoint, client_ip)``. State
disappears when the container restarts, which is fine for E2E tests; the
``POST /throttle/reset`` endpoint clears state explicitly between cases.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter()

_lock = threading.Lock()
# strict bucket: window of `RESET_AFTER_SEC` seconds, fixed limit per window.
_strict_state: dict[tuple[str, str], dict[str, float]] = {}
RESET_AFTER_SEC = 1.0
# burst bucket: token-bucket-ish counter per second.
_burst_state: dict[tuple[str, str], dict[str, float]] = {}


def _client_key(request: Request, scope: str) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    return (scope, ip)


@router.get("/strict")
def strict(
    request: Request,
    limit: int = Query(default=10, ge=1, le=10_000),
) -> JSONResponse:
    key = _client_key(request, "strict")
    now = time.time()
    with _lock:
        bucket = _strict_state.get(key)
        if bucket is None or now >= bucket["reset_at"]:
            bucket = {"count": 0, "reset_at": now + RESET_AFTER_SEC}
            _strict_state[key] = bucket
        bucket["count"] += 1
        used = int(bucket["count"])
        reset_at = float(bucket["reset_at"])

    remaining = max(0, limit - used)
    headers = {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(int(reset_at)),
    }
    if used > limit:
        headers["Retry-After"] = "1"
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited", "limit": limit},
            headers=headers,
        )
    return JSONResponse(
        content={"ok": True, "used": used, "limit": limit, "remaining": remaining},
        headers=headers,
    )


@router.get("/burst")
def burst(
    request: Request,
    per_second: int = Query(default=2, ge=1, le=1000),
) -> JSONResponse:
    key = _client_key(request, "burst")
    now = time.time()
    second = int(now)
    with _lock:
        bucket = _burst_state.get(key)
        if bucket is None or int(bucket["second"]) != second:
            bucket = {"second": float(second), "count": 0.0}
            _burst_state[key] = bucket
        bucket["count"] += 1
        used = int(bucket["count"])

    headers = {
        "X-RateLimit-Limit": str(per_second),
        "X-RateLimit-Remaining": str(max(0, per_second - used)),
        "X-RateLimit-Reset": str(second + 1),
    }
    if used > per_second:
        headers["Retry-After"] = "1"
        return JSONResponse(
            status_code=429,
            content={"error": "burst_exceeded", "per_second": per_second},
            headers=headers,
        )
    return JSONResponse(
        content={"ok": True, "used": used, "per_second": per_second},
        headers=headers,
    )


@router.post("/reset")
def reset(x_test_reset: Optional[str] = Header(default=None)) -> JSONResponse:
    if x_test_reset != "1":
        return JSONResponse(
            status_code=400,
            content={"error": "missing_X-Test-Reset_header"},
        )
    with _lock:
        _strict_state.clear()
        _burst_state.clear()
    return JSONResponse(content={"ok": True, "reset": True})
