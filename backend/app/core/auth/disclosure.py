"""Auth disclosure policy — typed boundary.

BE-GO-READY-AUTH-ENGINE-001-DISCLOSURE-POLICY-EXTRACTION.

Pure decision layer for auth-domain HTTP error disclosure. The
module owns *what* the client sees (status code, italian message,
machine-readable code, custom headers) for every auth-domain
denial / failure. It does **not** own:

- token verification / decoding (`app.shared.jwt_token`);
- password hashing (`app.shared.password_hashing`);
- MFA secret/code logic (`app.core.auth.mfa`);
- session / blacklist (`app.shared.token_blacklist`);
- rate-limit accounting (`app.shared.login_rate_limiter`);
- group / ownership checks (`app.core.auth.ownership`);
- DB queries (`app.db.*`).

Boundary contract
=================
- **Input**: stable Python primitives (int, bool, str). No
  `Request`, no DB row, no Pydantic model.
- **Output**: `DisclosureDecision` frozen dataclass — typed,
  hashable, easy to compare in tests.
- **Side effect** (only via `raise_disclosure`): raises a
  `HTTPException` whose `detail` is the dict-shape envelope
  `{error, message, details}` consumed by the central
  `app.main` handler. Status code and headers preserved.

Policy invariants (ADR-0022)
============================
The four anti-enumeration invariants are *encoded in the function
boundaries themselves*:

1. **Login bad creds vs user-not-exist** — single function
   `invalid_credentials()`. No way to call a different code for
   "user gone" vs "wrong password".
2. **Post-token user-deleted vs user-disabled** — single function
   `user_not_found_or_disabled()`. Same code, same message.
3. **Admin reset/delete out-of-group vs not-existing** —
   `admin_target_not_visible(is_superadmin)` returns 404 only
   for superadmin, otherwise uniform 403 access_denied. The
   call site cannot accidentally split the two.
4. **Account disabled distinct from invalid_credentials** —
   `account_disabled()` returns 403 (post-password-ok), distinct
   from `invalid_credentials()` 401 (LEAKY_BY_DESIGN, ADR-0018-style).

References
==========
- ADR-0022 §"Catalog: famiglia auth (27 codici)";
- ADR-0022 §"Anti-enumeration invariants (locked source-bound)";
- BE_AUTH_DOMAIN_ADR audit (40 call site → 27 promotion).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, NoReturn, Optional

from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http


@dataclass(frozen=True)
class DisclosureDecision:
    """Typed disclosure outcome for an auth-domain failure.

    Attributes:
        code: stable machine-readable error code from
            `app.shared.error_codes`. Visible to the client as
            `response.body.error` (and `response.body.detail.error`
            mirror).
        status_code: HTTP status preserved by ADR-0022.
        message: italian user-facing text exactly as historically
            emitted. Preserves anti-enumeration invariants and
            ADR-0018 / batch 014 disclosure decisions.
        headers: optional response headers. Used for
            `WWW-Authenticate: Bearer` (token-related 401s) and
            `Retry-After` (429 rate-limit). `None` means no custom
            headers.
    """

    code: str
    status_code: int
    message: str
    headers: Optional[Mapping[str, str]] = None


# ---------------------------------------------------------------------------
# Pre-auth — token presence/validity disclosure (401, WWW-Authenticate: Bearer)
# ---------------------------------------------------------------------------


_BEARER_HEADER: Mapping[str, str] = {"WWW-Authenticate": "Bearer"}


def token_missing() -> DisclosureDecision:
    """401 + `WWW-Authenticate: Bearer`. Bearer credential absent."""
    return DisclosureDecision(
        code=error_codes.AUTH_TOKEN_MISSING,
        status_code=401,
        message="Token mancante",
        headers=_BEARER_HEADER,
    )


def token_invalid() -> DisclosureDecision:
    """401 + `WWW-Authenticate: Bearer`. Token decode failed
    (invalid / expired / wrong signature). UNIFORM: no
    distinguishability between expired and invalid (would leak TTL).
    """
    return DisclosureDecision(
        code=error_codes.AUTH_TOKEN_INVALID,
        status_code=401,
        message="Token invalido o scaduto",
        headers=_BEARER_HEADER,
    )


def token_revoked() -> DisclosureDecision:
    """401 + `WWW-Authenticate: Bearer`. Token decoded OK but jti is
    blacklisted (logout). LEAKY_BY_DESIGN (UX confidence after logout)."""
    return DisclosureDecision(
        code=error_codes.AUTH_TOKEN_REVOKED,
        status_code=401,
        message="Token revocato",
        headers=_BEARER_HEADER,
    )


# ---------------------------------------------------------------------------
# Post-token user state — anti-enumeration #2 (UNIFORM 401)
# ---------------------------------------------------------------------------


def user_not_found_or_disabled() -> DisclosureDecision:
    """401 (no `WWW-Authenticate`). Token valid but the user record is
    missing or `is_active=False`. UNIFORM: a single decision for both
    branches — splitting would let an attacker enumerate which path
    the account ended up in.
    """
    return DisclosureDecision(
        code=error_codes.AUTH_USER_NOT_FOUND_OR_DISABLED,
        status_code=401,
        message="Utente non trovato o disabilitato",
    )


# ---------------------------------------------------------------------------
# Login — anti-enumeration #1 + #4 + ADR-0018 MFA trade-offs
# ---------------------------------------------------------------------------


def invalid_credentials() -> DisclosureDecision:
    """401. UNIFORM for "user does not exist" and "password wrong".
    Constant-time bcrypt verify (`_DUMMY_PASSWORD_HASH` in auth.py)
    is the timing counterpart to this code."""
    return DisclosureDecision(
        code=error_codes.AUTH_INVALID_CREDENTIALS,
        status_code=401,
        message="Username o password errati",
    )


def account_disabled() -> DisclosureDecision:
    """403. Post-password-ok branch: user exists, password verified,
    but `is_active=False`. LEAKY_BY_DESIGN distinct from
    `invalid_credentials()`."""
    return DisclosureDecision(
        code=error_codes.AUTH_ACCOUNT_DISABLED,
        status_code=403,
        message="Account disabilitato",
    )


def mfa_required() -> DisclosureDecision:
    """403. Post-password-ok branch: user has MFA enabled and
    `mfa_code` is missing. LEAKY_BY_DESIGN, ADR-0018 trade-off.
    Italian message preserved as the literal `mfa_required` for the
    SPA frontend that branches on this string."""
    return DisclosureDecision(
        code=error_codes.AUTH_MFA_REQUIRED,
        status_code=403,
        message="mfa_required",
    )


def mfa_invalid() -> DisclosureDecision:
    """401. Post-password-ok: TOTP supplied but wrong. Counts on
    rate-limit IP budget."""
    return DisclosureDecision(
        code=error_codes.AUTH_MFA_INVALID,
        status_code=401,
        message="mfa_invalid",
    )


def mfa_code_invalid() -> DisclosureDecision:
    """400. TOTP wrong during enroll/disable MFA. Distinct from
    `mfa_invalid()` (401, login)."""
    return DisclosureDecision(
        code=error_codes.AUTH_MFA_CODE_INVALID,
        status_code=400,
        message="Codice MFA non valido",
    )


def rate_limit_exceeded(retry_after_seconds: int) -> DisclosureDecision:
    """429 + `Retry-After`. Login lockout per IP. UNIFORM."""
    safe = max(int(retry_after_seconds), 1)
    return DisclosureDecision(
        code=error_codes.AUTH_RATE_LIMIT_EXCEEDED,
        status_code=429,
        message=f"Troppi tentativi. Riprova tra {safe} secondi.",
        headers={"Retry-After": str(safe)},
    )


def revocation_unavailable() -> DisclosureDecision:
    """503. Redis blacklist unavailable during logout. Fail-loud
    (ADR-0009)."""
    return DisclosureDecision(
        code=error_codes.AUTH_REVOCATION_UNAVAILABLE,
        status_code=503,
        message="Servizio di revoca token non disponibile, riprova",
    )


# ---------------------------------------------------------------------------
# Post-authz — role-based disclosure (403, LEAKY_BY_DESIGN UI-friendly)
# ---------------------------------------------------------------------------


def role_insufficient_for_write() -> DisclosureDecision:
    return DisclosureDecision(
        code=error_codes.AUTH_ROLE_INSUFFICIENT_FOR_WRITE,
        status_code=403,
        message="Permesso negato: i viewer/esecutori non possono creare/modificare risorse",
    )


def role_kiosk_only() -> DisclosureDecision:
    return DisclosureDecision(
        code=error_codes.AUTH_ROLE_KIOSK_ONLY,
        status_code=403,
        message="Permesso negato: questo endpoint non è accessibile al ruolo esecutore",
    )


def role_admin_required() -> DisclosureDecision:
    return DisclosureDecision(
        code=error_codes.AUTH_ROLE_ADMIN_REQUIRED,
        status_code=403,
        message="Permesso negato: richiesto ruolo admin",
    )


def role_superadmin_required() -> DisclosureDecision:
    return DisclosureDecision(
        code=error_codes.AUTH_ROLE_SUPERADMIN_REQUIRED,
        status_code=403,
        message="Permesso negato: richiesto ruolo superadmin",
    )


def role_create_privilege_required() -> DisclosureDecision:
    return DisclosureDecision(
        code=error_codes.AUTH_ROLE_CREATE_PRIVILEGE_REQUIRED,
        status_code=403,
        message="Solo un superadmin può creare utenti admin o superadmin",
    )


def field_role_restricted_group_id() -> DisclosureDecision:
    """403. Tentativo di modifica `group_id` da admin non-superadmin."""
    return DisclosureDecision(
        code=error_codes.AUTH_FIELD_ROLE_RESTRICTED,
        status_code=403,
        message="Solo superadmin puo' modificare group_id",
    )


# ---------------------------------------------------------------------------
# Self-action guards — UNIFORM
# ---------------------------------------------------------------------------


def self_modify_denied() -> DisclosureDecision:
    return DisclosureDecision(
        code=error_codes.AUTH_SELF_MODIFY_DENIED,
        status_code=403,
        message="Non puoi modificare il tuo stesso ruolo",
    )


def self_disable_denied() -> DisclosureDecision:
    return DisclosureDecision(
        code=error_codes.AUTH_SELF_DISABLE_DENIED,
        status_code=403,
        message="Non puoi disabilitare il tuo stesso account",
    )


def self_delete_denied() -> DisclosureDecision:
    return DisclosureDecision(
        code=error_codes.AUTH_SELF_DELETE_DENIED,
        status_code=400,
        message="Non puoi eliminare te stesso",
    )


# ---------------------------------------------------------------------------
# Anti-enumeration #3 — admin reset/delete on missing-or-out-of-group target
# ---------------------------------------------------------------------------


def access_denied() -> DisclosureDecision:
    """403 access_denied. Used as the UNIFORM 403 in the
    anti-enumeration #3 invariant (admin path) and elsewhere
    (group-ACL, path-traversal denial — batch 014 scope)."""
    return DisclosureDecision(
        code=error_codes.ACCESS_DENIED,
        status_code=403,
        message="Accesso negato",
    )


def admin_target_not_visible(*, is_superadmin: bool) -> DisclosureDecision:
    """Anti-enumeration #3 invariant.

    Admin reset_password / delete_user with a target that is either
    missing OR out-of-group:
    - **superadmin** — sees a real 404 `user_not_found` (privilege
      inherent to the role: superadmin can already enumerate the
      whole user table);
    - **admin non-superadmin** — sees uniform 403 `access_denied` so
      the missing-vs-out-of-group ambiguity cannot be exploited for
      enumeration.

    Encoding the decision in this single function prevents a future
    refactor from splitting the two branches into distinct codes.
    """
    if is_superadmin:
        return user_not_found()
    return access_denied()


# ---------------------------------------------------------------------------
# Generic 404 / 409 / 400 disclosure for auth-domain admin endpoints
# ---------------------------------------------------------------------------


def user_not_found() -> DisclosureDecision:
    return DisclosureDecision(
        code=error_codes.USER_NOT_FOUND,
        status_code=404,
        message="Utente non trovato",
    )


def group_not_found() -> DisclosureDecision:
    return DisclosureDecision(
        code=error_codes.GROUP_NOT_FOUND,
        status_code=404,
        message="Gruppo non trovato",
    )


def username_already_exists() -> DisclosureDecision:
    return DisclosureDecision(
        code=error_codes.USERNAME_ALREADY_EXISTS,
        status_code=409,
        message="Username già esistente",
    )


def group_id_already_exists() -> DisclosureDecision:
    return DisclosureDecision(
        code=error_codes.GROUP_ID_ALREADY_EXISTS,
        status_code=409,
        message="group_id già esistente",
    )


def group_default_not_deletable() -> DisclosureDecision:
    return DisclosureDecision(
        code=error_codes.GROUP_DEFAULT_NOT_DELETABLE,
        status_code=400,
        message="Il gruppo 'default' non può essere eliminato",
    )


# ---------------------------------------------------------------------------
# Ownership disclosure (403 LEAKY_BY_DESIGN, label-driven)
# ---------------------------------------------------------------------------


def hierarchical_owner_required(label: str) -> DisclosureDecision:
    """403. Resource was created by a superadmin: only a superadmin
    can modify it. UI-friendly explanation of *why* the admin of the
    group cannot act."""
    if not isinstance(label, str) or not label:
        raise ValueError("label must be a non-empty string")
    return DisclosureDecision(
        code=error_codes.AUTH_HIERARCHICAL_OWNER_REQUIRED,
        status_code=403,
        message=f"{label} creata da un superadmin: solo un superadmin può modificarla",
    )


def owner_only_required(resource_label: str) -> DisclosureDecision:
    """403. The resource requires the original owner (`created_by`)
    or an admin/superadmin. UI-friendly per-resource phrasing."""
    if not isinstance(resource_label, str) or not resource_label:
        raise ValueError("resource_label must be a non-empty string")
    return DisclosureDecision(
        code=error_codes.AUTH_OWNER_ONLY,
        status_code=403,
        message=f"Permesso negato: solo l'owner del {resource_label.lower()} o un admin può eseguire questa operazione",
    )


# ---------------------------------------------------------------------------
# Side-effecting boundary: raise the decision as HTTPException
# ---------------------------------------------------------------------------


def raise_disclosure(decision: DisclosureDecision) -> NoReturn:
    """Apply a `DisclosureDecision`: raise an `HTTPException` whose
    `detail` is the dict-shape envelope `{error, message, details}`
    consumed by the central `app.main` handler.

    All call sites use `raise_typed_http` (which now supports
    `headers=...` post-Codex review of batch 016). Status code,
    italian message and any custom headers are preserved exactly.

    The pure `DisclosureDecision` value object remains the unit of
    test for policy assertions; this function is the only side-effect
    sink and is exercised by integration-style tests via the
    real `HTTPException` chain.
    """
    raise_typed_http(
        code=decision.code,
        status_code=decision.status_code,
        message=decision.message,
        headers=dict(decision.headers) if decision.headers else None,
    )
