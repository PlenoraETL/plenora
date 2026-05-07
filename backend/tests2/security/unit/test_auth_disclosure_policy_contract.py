"""Contract test BE-GO-READY-AUTH-ENGINE-001-DISCLOSURE-POLICY-EXTRACTION.

Pure-decision contract test for `app.core.auth.disclosure`. Verifies:

1. **Per-policy decision lock**: each pure function returns the
   exact `(code, status_code, message, headers)` tuple historically
   emitted by `auth.py` / `ownership.py`. Drift = silent disclosure
   regression.
2. **Anti-enumeration invariant encoding**:
   a. `invalid_credentials()` is a *single* function — no per-branch
      variant. Login bad-creds vs user-not-exist must collapse here.
   b. `user_not_found_or_disabled()` is a *single* function — post-token
      user-deleted vs user-disabled must collapse.
   c. `admin_target_not_visible(is_superadmin=...)` returns
      USER_NOT_FOUND only for superadmin, else uniform ACCESS_DENIED.
3. **Header preservation**: WWW-Authenticate: Bearer on the 4 token
   sites; Retry-After on the rate-limit 429.
4. **`raise_disclosure` boundary**: applies the decision via
   `raise_typed_http` (with headers when set), preserving status,
   message, headers, and the structured detail envelope shape.
5. **Source-bound delegation lock**: `app/core/auth/auth.py` and
   `ownership.py` no longer hold inline `raise_typed_http(code=...)`
   calls for auth-domain disclosures — they delegate to the
   disclosure module.
"""

from __future__ import annotations

import os
# Garantisce DATABASE_URL prima di qualunque import che possa caricare
# `app.db.pg_connection` (raise RuntimeError al module-level se assente).
os.environ.setdefault(
    "DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test"
)

import re
from pathlib import Path
from typing import Tuple

import pytest
from fastapi import HTTPException

from app.core.auth import disclosure
from app.shared import error_codes


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_AUTH_PY = _BACKEND_ROOT / "app" / "core" / "auth" / "auth.py"
_OWNERSHIP_PY = _BACKEND_ROOT / "app" / "core" / "auth" / "ownership.py"
_DISCLOSURE_PY = _BACKEND_ROOT / "app" / "core" / "auth" / "disclosure.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# 1. Per-policy decision lock


# (factory_callable, expected_code, expected_status, expected_message,
#  expected_headers_or_None).
_BEARER = {"WWW-Authenticate": "Bearer"}

_ZERO_ARG_DECISIONS = [
    # token-related (401 + WWW-Authenticate)
    (
        disclosure.token_missing,
        error_codes.AUTH_TOKEN_MISSING,
        401,
        "Token mancante",
        _BEARER,
    ),
    (
        disclosure.token_invalid,
        error_codes.AUTH_TOKEN_INVALID,
        401,
        "Token invalido o scaduto",
        _BEARER,
    ),
    (
        disclosure.token_revoked,
        error_codes.AUTH_TOKEN_REVOKED,
        401,
        "Token revocato",
        _BEARER,
    ),
    # post-token user state (anti-enum #2 UNIFORM)
    (
        disclosure.user_not_found_or_disabled,
        error_codes.AUTH_USER_NOT_FOUND_OR_DISABLED,
        401,
        "Utente non trovato o disabilitato",
        None,
    ),
    # login (anti-enum #1 UNIFORM + #4 LEAKY_BY_DESIGN)
    (
        disclosure.invalid_credentials,
        error_codes.AUTH_INVALID_CREDENTIALS,
        401,
        "Username o password errati",
        None,
    ),
    (
        disclosure.account_disabled,
        error_codes.AUTH_ACCOUNT_DISABLED,
        403,
        "Account disabilitato",
        None,
    ),
    (
        disclosure.mfa_required,
        error_codes.AUTH_MFA_REQUIRED,
        403,
        "mfa_required",
        None,
    ),
    (
        disclosure.mfa_invalid,
        error_codes.AUTH_MFA_INVALID,
        401,
        "mfa_invalid",
        None,
    ),
    (
        disclosure.mfa_code_invalid,
        error_codes.AUTH_MFA_CODE_INVALID,
        400,
        "Codice MFA non valido",
        None,
    ),
    (
        disclosure.revocation_unavailable,
        error_codes.AUTH_REVOCATION_UNAVAILABLE,
        503,
        "Servizio di revoca token non disponibile, riprova",
        None,
    ),
    # role-based 403
    (
        disclosure.role_insufficient_for_write,
        error_codes.AUTH_ROLE_INSUFFICIENT_FOR_WRITE,
        403,
        "Permesso negato: i viewer/esecutori non possono "
        "creare/modificare risorse",
        None,
    ),
    (
        disclosure.role_kiosk_only,
        error_codes.AUTH_ROLE_KIOSK_ONLY,
        403,
        "Permesso negato: questo endpoint non è accessibile al "
        "ruolo esecutore",
        None,
    ),
    (
        disclosure.role_admin_required,
        error_codes.AUTH_ROLE_ADMIN_REQUIRED,
        403,
        "Permesso negato: richiesto ruolo admin",
        None,
    ),
    (
        disclosure.role_superadmin_required,
        error_codes.AUTH_ROLE_SUPERADMIN_REQUIRED,
        403,
        "Permesso negato: richiesto ruolo superadmin",
        None,
    ),
    (
        disclosure.role_create_privilege_required,
        error_codes.AUTH_ROLE_CREATE_PRIVILEGE_REQUIRED,
        403,
        "Solo un superadmin può creare utenti admin o superadmin",
        None,
    ),
    (
        disclosure.field_role_restricted_group_id,
        error_codes.AUTH_FIELD_ROLE_RESTRICTED,
        403,
        "Solo superadmin puo' modificare group_id",
        None,
    ),
    # self-action guards
    (
        disclosure.self_modify_denied,
        error_codes.AUTH_SELF_MODIFY_DENIED,
        403,
        "Non puoi modificare il tuo stesso ruolo",
        None,
    ),
    (
        disclosure.self_disable_denied,
        error_codes.AUTH_SELF_DISABLE_DENIED,
        403,
        "Non puoi disabilitare il tuo stesso account",
        None,
    ),
    (
        disclosure.self_delete_denied,
        error_codes.AUTH_SELF_DELETE_DENIED,
        400,
        "Non puoi eliminare te stesso",
        None,
    ),
    # generic admin-domain
    (
        disclosure.access_denied,
        error_codes.ACCESS_DENIED,
        403,
        "Accesso negato",
        None,
    ),
    (
        disclosure.user_not_found,
        error_codes.USER_NOT_FOUND,
        404,
        "Utente non trovato",
        None,
    ),
    (
        disclosure.group_not_found,
        error_codes.GROUP_NOT_FOUND,
        404,
        "Gruppo non trovato",
        None,
    ),
    (
        disclosure.username_already_exists,
        error_codes.USERNAME_ALREADY_EXISTS,
        409,
        "Username già esistente",
        None,
    ),
    (
        disclosure.group_id_already_exists,
        error_codes.GROUP_ID_ALREADY_EXISTS,
        409,
        "group_id già esistente",
        None,
    ),
    (
        disclosure.group_default_not_deletable,
        error_codes.GROUP_DEFAULT_NOT_DELETABLE,
        400,
        "Il gruppo 'default' non può essere eliminato",
        None,
    ),
]


@pytest.mark.parametrize(
    "factory,expected_code,expected_status,expected_message,expected_headers",
    _ZERO_ARG_DECISIONS,
)
def test_zero_arg_decision_returns_locked_tuple(
    factory, expected_code, expected_status, expected_message, expected_headers
):
    """Per ogni policy decision senza argomenti: la tupla (code, status,
    message, headers) e' lockata source-bound. Drift = disclosure
    regression."""
    decision = factory()
    assert decision.code == expected_code
    assert decision.status_code == expected_status
    assert decision.message == expected_message
    if expected_headers is None:
        assert decision.headers is None
    else:
        assert dict(decision.headers) == expected_headers


def test_rate_limit_exceeded_decision_includes_retry_after():
    """Rate-limit decision must carry Retry-After header with the
    int value (clamped >= 1) and the message must include the
    retry-after seconds."""
    decision = disclosure.rate_limit_exceeded(42)
    assert decision.code == error_codes.AUTH_RATE_LIMIT_EXCEEDED
    assert decision.status_code == 429
    assert decision.message == "Troppi tentativi. Riprova tra 42 secondi."
    assert dict(decision.headers) == {"Retry-After": "42"}


def test_rate_limit_exceeded_clamps_retry_after_to_min_one():
    """Retry-After must clamp to >= 1 even for 0 / negative values
    (matches historical inline behavior in auth.py:_check_rate_limit_in_memory)."""
    for raw in (0, -5, -1):
        decision = disclosure.rate_limit_exceeded(raw)
        assert dict(decision.headers) == {"Retry-After": "1"}
        assert decision.message == "Troppi tentativi. Riprova tra 1 secondi."


# 2. Anti-enumeration invariant encoding


def test_admin_target_not_visible_uniform_for_admin():
    """Anti-enum #3: admin reset_password / delete_user with target
    missing-or-out-of-group MUST emit uniform 403 ACCESS_DENIED."""
    decision = disclosure.admin_target_not_visible(is_superadmin=False)
    assert decision.code == error_codes.ACCESS_DENIED
    assert decision.status_code == 403
    assert decision.message == "Accesso negato"
    assert decision.headers is None


def test_admin_target_not_visible_distinct_for_superadmin():
    """Anti-enum #3: only superadmin sees real 404 USER_NOT_FOUND
    (privilege inherent to the role)."""
    decision = disclosure.admin_target_not_visible(is_superadmin=True)
    assert decision.code == error_codes.USER_NOT_FOUND
    assert decision.status_code == 404
    assert decision.message == "Utente non trovato"
    assert decision.headers is None


def test_admin_target_not_visible_only_two_outcomes():
    """The function must return either USER_NOT_FOUND or ACCESS_DENIED.
    Encoded in source so a future refactor cannot split into a third
    code (would break anti-enum)."""
    src = _read(_DISCLOSURE_PY)
    fn_match = re.search(
        r"def admin_target_not_visible\([^)]*\)[^:]*:.+?(?=\n\ndef |\Z)",
        src,
        flags=re.DOTALL,
    )
    assert fn_match is not None
    body = fn_match.group(0)
    # Only two return statements: user_not_found() and access_denied().
    returns = re.findall(r"return\s+(\w+\(\))", body)
    assert returns == ["user_not_found()", "access_denied()"], (
        f"admin_target_not_visible body has unexpected return shape: {returns}"
    )


def test_invalid_credentials_is_single_function():
    """Anti-enum #1: there must be exactly ONE callable for "user
    inesistente / password errata" — splitting would leak the branch."""
    # Source-bound: only one definition with this name in the module.
    src = _read(_DISCLOSURE_PY)
    defs = re.findall(r"^def\s+invalid_credentials\b", src, flags=re.MULTILINE)
    assert len(defs) == 1


def test_user_not_found_or_disabled_is_single_function():
    """Anti-enum #2: post-token user-deleted vs user-disabled must
    collapse into one decision."""
    src = _read(_DISCLOSURE_PY)
    defs = re.findall(
        r"^def\s+user_not_found_or_disabled\b", src, flags=re.MULTILINE
    )
    assert len(defs) == 1


# 3. Header preservation


def test_token_factories_carry_www_authenticate_bearer():
    """The 3 token-related 401 sites must carry WWW-Authenticate:
    Bearer (RFC 6750)."""
    for factory in (
        disclosure.token_missing,
        disclosure.token_invalid,
        disclosure.token_revoked,
    ):
        decision = factory()
        assert decision.headers is not None
        assert dict(decision.headers) == {"WWW-Authenticate": "Bearer"}


def test_user_not_found_or_disabled_has_no_www_authenticate():
    """Historically the post-token branch does NOT emit
    WWW-Authenticate (the request *was* authenticated)."""
    decision = disclosure.user_not_found_or_disabled()
    assert decision.headers is None


# 4. raise_disclosure boundary


def test_raise_disclosure_raises_http_exception_with_typed_detail():
    """raise_disclosure must produce HTTPException whose detail is the
    structured `{error, message, details}` dict (envelope shape)."""
    with pytest.raises(HTTPException) as exc_info:
        disclosure.raise_disclosure(disclosure.access_denied())
    exc = exc_info.value
    assert exc.status_code == 403
    assert isinstance(exc.detail, dict)
    assert exc.detail["error"] == "access_denied"
    assert exc.detail["message"] == "Accesso negato"
    assert exc.detail["details"] == {}


def test_raise_disclosure_preserves_www_authenticate_header():
    """For token-related decisions, raise_disclosure must attach
    WWW-Authenticate: Bearer."""
    with pytest.raises(HTTPException) as exc_info:
        disclosure.raise_disclosure(disclosure.token_revoked())
    exc = exc_info.value
    assert exc.status_code == 401
    assert exc.headers is not None
    assert exc.headers["WWW-Authenticate"] == "Bearer"
    assert exc.detail["error"] == "auth_token_revoked"
    assert exc.detail["message"] == "Token revocato"


def test_raise_disclosure_preserves_retry_after_header():
    """Rate-limit decision must surface Retry-After header verbatim."""
    with pytest.raises(HTTPException) as exc_info:
        disclosure.raise_disclosure(disclosure.rate_limit_exceeded(7))
    exc = exc_info.value
    assert exc.status_code == 429
    assert exc.headers["Retry-After"] == "7"
    assert exc.detail["error"] == "auth_rate_limit_exceeded"


# 5. Source-bound delegation lock


_AUTH_DOMAIN_CODE_NAMES = [
    "AUTH_TOKEN_MISSING",
    "AUTH_TOKEN_INVALID",
    "AUTH_TOKEN_REVOKED",
    "AUTH_USER_NOT_FOUND_OR_DISABLED",
    "AUTH_INVALID_CREDENTIALS",
    "AUTH_MFA_INVALID",
    "AUTH_ACCOUNT_DISABLED",
    "AUTH_MFA_REQUIRED",
    "AUTH_ROLE_INSUFFICIENT_FOR_WRITE",
    "AUTH_ROLE_KIOSK_ONLY",
    "AUTH_ROLE_ADMIN_REQUIRED",
    "AUTH_ROLE_SUPERADMIN_REQUIRED",
    "AUTH_ROLE_CREATE_PRIVILEGE_REQUIRED",
    "AUTH_FIELD_ROLE_RESTRICTED",
    "AUTH_SELF_MODIFY_DENIED",
    "AUTH_SELF_DISABLE_DENIED",
    "AUTH_SELF_DELETE_DENIED",
    "AUTH_HIERARCHICAL_OWNER_REQUIRED",
    "AUTH_OWNER_ONLY",
    "AUTH_MFA_CODE_INVALID",
    "AUTH_REVOCATION_UNAVAILABLE",
    "AUTH_RATE_LIMIT_EXCEEDED",
    "USER_NOT_FOUND",
    "GROUP_NOT_FOUND",
    "USERNAME_ALREADY_EXISTS",
    "GROUP_ID_ALREADY_EXISTS",
    "GROUP_DEFAULT_NOT_DELETABLE",
]


def _count_constant_refs(text: str, const_name: str) -> int:
    """Count occurrences of `error_codes.<CONST>` literal in source."""
    pattern = re.compile(rf"\berror_codes\.{re.escape(const_name)}\b")
    return len(pattern.findall(text))


@pytest.mark.parametrize("const_name", _AUTH_DOMAIN_CODE_NAMES)
def test_auth_py_does_not_inline_auth_domain_code(const_name: str):
    """Source-bound delegation lock for `auth.py`.

    Post BE-GO-READY-AUTH-ENGINE-001: every auth-domain disclosure
    code must be referenced ONLY via the disclosure module, not as
    `error_codes.<CONST>` inline in `auth.py`. Drift = a call site
    that bypassed the policy boundary.

    `ACCESS_DENIED` and `USER_NOT_FOUND` are the only exceptions:
    they remain in the catalog but `auth.py` accesses them through
    `disclosure.admin_target_not_visible(...)` / `access_denied()` /
    `user_not_found()`, never as inline `error_codes.<CONST>`.
    """
    text = _read(_AUTH_PY)
    count = _count_constant_refs(text, const_name)
    assert count == 0, (
        f"auth.py still references error_codes.{const_name} inline "
        f"({count}x). All auth-domain disclosures must delegate to "
        f"app.core.auth.disclosure."
    )


@pytest.mark.parametrize(
    "const_name",
    [
        "ACCESS_DENIED",
        "AUTH_HIERARCHICAL_OWNER_REQUIRED",
        "AUTH_OWNER_ONLY",
    ],
)
def test_ownership_py_does_not_inline_auth_domain_code(const_name: str):
    """Same as above for `ownership.py` — the 3 auth-domain codes
    raised here must go through the disclosure module."""
    text = _read(_OWNERSHIP_PY)
    count = _count_constant_refs(text, const_name)
    assert count == 0, (
        f"ownership.py still references error_codes.{const_name} inline "
        f"({count}x). Must delegate via app.core.auth.disclosure."
    )


def test_auth_py_imports_disclosure_module():
    """auth.py must import `app.core.auth.disclosure` to delegate
    policy decisions. The actual usage is locked by the per-const
    tests above; this is the import-presence sanity check."""
    text = _read(_AUTH_PY)
    assert "from app.core.auth import disclosure" in text


def test_ownership_py_imports_disclosure_module():
    """Symmetric for ownership.py."""
    text = _read(_OWNERSHIP_PY)
    assert "from app.core.auth import disclosure" in text


def test_disclosure_module_does_not_import_db_or_router():
    """Boundary purity check: disclosure module must NOT touch DB,
    router, request, or any side-effecting auth machinery. Only
    `error_codes` + `error_envelope.raise_typed_http` are allowed."""
    text = _read(_DISCLOSURE_PY)
    forbidden = [
        "import app.db.",
        "from app.db.",
        "from fastapi import",  # routers/Depends/Request out of scope
        "import app.core.auth.auth",
        "from app.core.auth.auth",
        "import app.core.auth.ownership",
        "import app.core.auth.mfa",
        "import app.shared.token_blacklist",
        "import app.shared.password_hashing",
        "import app.shared.login_rate_limiter",
    ]
    for line in forbidden:
        assert line not in text, (
            f"disclosure.py must remain pure (no side-effecting "
            f"imports). Found forbidden import: {line!r}"
        )
