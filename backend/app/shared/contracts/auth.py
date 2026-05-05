"""Auth shared contract — pure types + pure policy.

BE-GO-READY-AUTH-ENGINE-012-SHARED-CONTRACT-BOUNDARY.

This module is the stable contract surface for auth-related types
and pure policy helpers. Routers and services that need to type-
annotate `CurrentUser` parameters, check role membership, or
filter by group should import from THIS module instead of from
`app.core.auth.auth`. The auth implementation package
(`app.core.auth.auth` + facade re-exports) stays the home of
FastAPI Depends, DB-bound dependencies, MFA, JWT, rate limiting,
and disclosure machinery — none of which belong in a contract.

Purity rules
============
This module MUST stay pure. It MUST NOT import:
- `fastapi` / `starlette`
- `app.db.*` / `psycopg2` / `redis`
- `app.config.settings`
- `app.core.auth.disclosure` (raises HTTPException)
- `app.core.auth.ownership` (DB-bound — `assert_*` helpers run
  `db.get_user`)
- `app.core.auth.mfa` / `app.shared.token_blacklist`
- `boto3` / `app.shared.cloud_backends`

Allowed dependencies:
- `app.core.auth.models` (pure pydantic leaf — `CurrentUser` and
  the 12 other DTOs)
- `app.core.auth.roles` (pure constants leaf)
- stdlib

Symbols exposed
===============
- ``CurrentUser`` — re-export from `app.core.auth.models`. Stable
  type for `Depends` annotations and worker-side construction.
- ``VALID_ROLES`` / ``ROLES_ABOVE_VIEWER`` /
  ``ROLES_ADMIN_OR_ABOVE`` / ``ROLES_KIOSK_ONLY`` — re-export
  from `app.core.auth.roles`. Tuple constants; stable.
- ``group_filter(current_user) -> Optional[str]`` — defined here
  (pure). Returns `None` for superadmin, else
  `current_user.group_id`. Pre-Phase-7 lived in
  `app.core.auth.ownership`; canonical home moved to this contract
  module to keep the contract pure (ownership.py drags
  `app.db.db_service` for `compute_can_edit` / `assert_can_modify`,
  which would violate this module's purity rule). `ownership.py`
  re-exports `group_filter` from here for KEEP_COMPAT; identity
  preserved (`auth.group_filter is ownership.group_filter is
  contract.group_filter`).

Symbols intentionally NOT exposed (per task brief)
===================================================
- ``get_current_user`` / ``require_*`` — FastAPI Depends; need
  decode_token + blacklist read + db.get_user + disclosure raise.
- ``require_*_owner`` — Depends + per-resource DB lookup.
- ``assert_group_access`` / ``assert_resource_visible`` /
  ``assert_can_modify`` / ``compute_can_edit`` — DB-bound or
  HTTPException-raising; live in `app.core.auth.ownership`.
- ``hash_password`` / ``verify_password`` /
  ``create_access_token`` / ``decode_token`` — implementation
  helpers (already on leaves: `app.shared.password_hashing` /
  `app.shared.jwt_token`).
- ``mfa.*``, ``is_jti_blacklisted`` / ``blacklist_jti``,
  ``_check_rate_limit*`` — stateful / Redis-bound.

Adding a symbol to this module is a contract change. Do not add
implementation symbols. The Phase-5 audit
(`.agent-temp/BE_AUTH_ENGINE_AUDIT.md` §"future contract
boundary") enumerates the safe-next vs not-yet split.
"""

from __future__ import annotations

from typing import Optional

from app.core.auth.models import CurrentUser
from app.core.auth.roles import (
    ROLES_ABOVE_VIEWER,
    ROLES_ADMIN_OR_ABOVE,
    ROLES_KIOSK_ONLY,
    VALID_ROLES,
)


__all__ = [
    "CurrentUser",
    "VALID_ROLES",
    "ROLES_ABOVE_VIEWER",
    "ROLES_ADMIN_OR_ABOVE",
    "ROLES_KIOSK_ONLY",
    "group_filter",
]


def group_filter(current_user: CurrentUser) -> Optional[str]:
    """Ritorna il group_id da usare come filtro.

    Pure policy:
      - superadmin → ``None`` (nessun filtro, vede tutto);
      - altri → ``current_user.group_id``.

    Canonical home: this contract module. `app.core.auth.ownership`
    re-exports for KEEP_COMPAT (ownership cannot define it directly
    without dragging `app.db.db_service` into the contract chain).
    `app.core.auth.auth` re-exports for KEEP_COMPAT through
    ownership. Identity preserved across all three surfaces:
    `auth.group_filter is ownership.group_filter is
    contract.group_filter` — locked by
    `tests2/security/unit/test_auth_facade_strangler_contract.py::
    test_group_filter_leaf_canonical_home`.
    """
    if current_user.role == "superadmin":
        return None
    return current_user.group_id
