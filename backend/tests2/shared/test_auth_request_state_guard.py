"""Architectural guard for the auth `request.state.*` cleanup.

BE-GO-READY-SECURITY-ENGINE-002-AUTH-CLEANUP-RESIDUALS.

Prevents the 4 dead `request.state.{group_id,user_id,username,role}`
fields from coming back. They were removed from `JWTMiddleware`
because they had zero non-middleware/non-test consumers; the
canonical surface for auth correlation is the `CurrentUser` returned
by `Depends(get_current_user)` (router-level) plus the ContextVar
`set_log_group` / `set_user_id` (logger-level).

Allowlist
=========
- `app/core/auth/request_id_middleware.py` may write
  `request.state.request_id` — that field is a separate, legitimate
  surface used by RequestIdMiddleware for X-Request-ID propagation.
- `tests2/security/unit/test_c8_observability_contract.py` may read
  `request.state.request_id` — this is the only surviving consumer
  and tests the RequestId pipeline, NOT the JWT auth path.

Anything else accessing `request.state.{group_id,user_id,username,role}`
is a regression: the symbol does not exist anymore at runtime, and
re-introducing it would re-create the dead-write situation. Use
`Depends(get_current_user) -> CurrentUser` instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_FORBIDDEN_FIELDS = ("group_id", "user_id", "username", "role")
_FORBIDDEN_PATTERNS = tuple(
    f"request.state.{name}" for name in _FORBIDDEN_FIELDS
)
# Same set with the alternative `getattr(request.state, "<name>", ...)`
# spelling. Anchored on the field name so we don't false-positive on
# unrelated `getattr` calls.
_FORBIDDEN_GETATTR = tuple(
    f'getattr(request.state, "{name}"' for name in _FORBIDDEN_FIELDS
)


def _iter_py_under(root: Path):
    for path in root.rglob("*.py"):
        # Skip caches.
        if "__pycache__" in path.parts:
            continue
        yield path


def _scan_for_dead_writes(root: Path) -> list[tuple[str, int, str]]:
    """Return [(rel_path, line_number, line_text)] for every match of
    `request.state.{group_id|user_id|username|role}` or the equivalent
    `getattr(request.state, "<name>", ...)` form."""
    hits: list[tuple[str, int, str]] = []
    for path in _iter_py_under(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            # Skip pure comments — the docstring of this very test
            # mentions the forbidden patterns; allowlist comments to
            # avoid recursive false positives.
            if stripped.startswith("#"):
                continue
            for pat in _FORBIDDEN_PATTERNS + _FORBIDDEN_GETATTR:
                if pat in line:
                    rel = str(path.relative_to(_BACKEND_ROOT)).replace("\\", "/")
                    hits.append((rel, lineno, line.rstrip()))
                    break
    return hits


def test_auth_middleware_no_longer_writes_request_state_auth_fields():
    """Source-bound: `JWTMiddleware` does NOT assign to any of the 4
    auth `request.state.*` fields."""
    src = (_BACKEND_ROOT / "app" / "core" / "auth" / "auth_middleware.py").read_text(
        encoding="utf-8"
    )
    for field in _FORBIDDEN_FIELDS:
        forbidden = f"request.state.{field} ="
        assert forbidden not in src, (
            f"app/core/auth/auth_middleware.py still assigns "
            f"`{forbidden} ...`. The 4 auth fields on request.state were "
            "removed because they had zero non-middleware consumers. "
            "Use `Depends(get_current_user)` for auth context instead."
        )
    # Also no helper that clears them.
    assert "_clear_request_state" not in src, (
        "Helper `_clear_request_state` was removed alongside the dead "
        "writes; reintroducing it suggests the dead writes are coming back."
    )


def test_no_consumers_of_request_state_auth_fields_anywhere():
    """Source-bound: NO file under `backend/app/` or `backend/workers/`
    or `backend/tests2/` reads or writes `request.state.{group_id,
    user_id, username, role}`.

    The RequestId middleware uses `request.state.request_id` which is
    a different surface and NOT in scope of this guard.
    """
    hits: list[tuple[str, int, str]] = []
    for sub in ("app", "workers", "tests2"):
        root = _BACKEND_ROOT / sub
        if not root.exists():
            continue
        hits.extend(_scan_for_dead_writes(root))

    # Filter the architectural guard test itself — its own docstring
    # mentions the forbidden patterns. Any non-comment match here would
    # be a real regression.
    self_path = "tests2/shared/test_auth_request_state_guard.py"
    real_hits = [(p, n, l) for (p, n, l) in hits if p != self_path]

    if real_hits:
        msg = "\n".join(f"  {p}:{n}: {l}" for (p, n, l) in real_hits)
        raise AssertionError(
            "request.state.{group_id,user_id,username,role} consumers "
            f"detected (regression):\n{msg}\n\n"
            "The 4 auth fields were removed from JWTMiddleware in "
            "BE-GO-READY-SECURITY-ENGINE-002. Use "
            "`Depends(get_current_user) -> CurrentUser` for auth "
            "context (router-level) or `set_log_group/set_user_id` "
            "ContextVar (logger-level)."
        )


def test_request_id_state_remains_legitimate():
    """Sanity: `request.state.request_id` (NOT in scope) is still
    written by RequestIdMiddleware. This isolates the guard from the
    legitimate RequestId surface."""
    src = (
        _BACKEND_ROOT / "app" / "core" / "auth" / "request_id_middleware.py"
    ).read_text(encoding="utf-8")
    assert "request.state.request_id" in src, (
        "RequestIdMiddleware must still write `request.state.request_id`. "
        "If it was removed, the auth_middleware cleanup spilled into the "
        "wrong middleware."
    )


def test_jwt_middleware_propagates_to_contextvars_only():
    """Source-bound: JWTMiddleware uses ContextVar setters (the
    canonical surface) and does NOT use any `request.state.*` write
    for auth context."""
    src = (
        _BACKEND_ROOT / "app" / "core" / "auth" / "auth_middleware.py"
    ).read_text(encoding="utf-8")
    # Canonical surface present.
    assert "set_log_group" in src
    assert "set_user_id" in src
    # No request.state writes for auth fields.
    for field in _FORBIDDEN_FIELDS:
        assert f"request.state.{field}" not in src, (
            f"JWTMiddleware references request.state.{field}; "
            "it should propagate auth ONLY via ContextVar."
        )
