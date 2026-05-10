"""
Contract test BE-GO-READY-AUTH-DOMAIN-PROMOTION-001.

Source-bound: parsa `app/core/auth/auth.py`, `ownership.py`, e
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
from typing import List, Tuple

import pytest

from app.shared import error_codes


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_AUTH_PY = _BACKEND_ROOT / "app" / "core" / "auth" / "auth.py"
_OWNERSHIP_PY = _BACKEND_ROOT / "app" / "core" / "auth" / "ownership.py"
_DISCLOSURE_PY = _BACKEND_ROOT / "app" / "core" / "auth" / "disclosure.py"
# BE-GO-READY-AUTH-ENGINE-003: i 5 endpoint admin CRUD su /auth/users
# sono stati estratti da `auth.py` in `users_router.py`. La superficie
# auth da scansionare per le anti-enum invariants comprende ora
# entrambi i file.
_USERS_ROUTER_PY = _BACKEND_ROOT / "app" / "core" / "auth" / "users_router.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# 1. Per-site code + message lock — disclosure.py


# (constant_name, italian_message_substring) per ogni decisione esposta
# da `disclosure.py`. Lock minimo source-bound: la costante e il messaggio
# italiano devono apparire entrambi nel sorgente. Non si lockano i numeri
# di riga (brittle al refactor).
_DISCLOSURE_LOCKS: List[Tuple[str, str]] = [
    # 401 token-related with WWW-Authenticate header.
    ("AUTH_TOKEN_MISSING", "Token mancante"),
    ("AUTH_TOKEN_INVALID", "Token invalido o scaduto"),
    ("AUTH_TOKEN_REVOKED", "Token revocato"),
    # 401 post-token user-not-found-or-disabled (no WWW-Authenticate).
    ("AUTH_USER_NOT_FOUND_OR_DISABLED", "Utente non trovato o disabilitato"),
    # 401 login bad creds.
    ("AUTH_INVALID_CREDENTIALS", "Username o password errati"),
    # 401 mfa invalid (post-password-ok, login).
    ("AUTH_MFA_INVALID", "mfa_invalid"),
    # 403 account disabled (post-password-ok).
    ("AUTH_ACCOUNT_DISABLED", "Account disabilitato"),
    # 403 mfa required (post-password-ok).
    ("AUTH_MFA_REQUIRED", "mfa_required"),
    # 403 role-based.
    ("AUTH_ROLE_INSUFFICIENT_FOR_WRITE",
     "Permesso negato: i viewer/esecutori non possono creare/modificare risorse"),
    ("AUTH_ROLE_KIOSK_ONLY",
     "Permesso negato: questo endpoint non è accessibile al ruolo esecutore"),
    ("AUTH_ROLE_ADMIN_REQUIRED", "Permesso negato: richiesto ruolo admin"),
    ("AUTH_ROLE_SUPERADMIN_REQUIRED",
     "Permesso negato: richiesto ruolo superadmin"),
    ("AUTH_ROLE_CREATE_PRIVILEGE_REQUIRED",
     "Solo un superadmin può creare utenti admin o superadmin"),
    ("AUTH_FIELD_ROLE_RESTRICTED", "Solo superadmin puo' modificare group_id"),
    ("AUTH_SELF_MODIFY_DENIED", "Non puoi modificare il tuo stesso ruolo"),
    ("AUTH_SELF_DISABLE_DENIED", "Non puoi disabilitare il tuo stesso account"),
    # 404 user/group not-found.
    ("USER_NOT_FOUND", "Utente non trovato"),
    ("GROUP_NOT_FOUND", "Gruppo non trovato"),
    # 409 conflict.
    ("USERNAME_ALREADY_EXISTS", "Username già esistente"),
    ("GROUP_ID_ALREADY_EXISTS", "group_id già esistente"),
    # 400 validation auth-related.
    ("GROUP_DEFAULT_NOT_DELETABLE",
     "Il gruppo 'default' non può essere eliminato"),
    ("AUTH_MFA_CODE_INVALID", "Codice MFA non valido"),
    ("AUTH_SELF_DELETE_DENIED", "Non puoi eliminare te stesso"),
    # 429 rate-limit with Retry-After.
    ("AUTH_RATE_LIMIT_EXCEEDED", "Troppi tentativi. Riprova tra"),
    # 503 infra fail-loud (revocation).
    ("AUTH_REVOCATION_UNAVAILABLE",
     "Servizio di revoca token non disponibile, riprova"),
]


@pytest.mark.parametrize("const_name,message_substr", _DISCLOSURE_LOCKS)
def test_disclosure_promotion_constant_and_message(
    const_name: str, message_substr: str
) -> None:
    """Per ogni decisione di policy auth-domain: la costante catalog
    appare e il messaggio italiano user-facing e' preservato in
    `disclosure.py` (il policy boundary Current contract 018)."""
    text = _read(_DISCLOSURE_PY)
    assert f"error_codes.{const_name}" in text, (
        f"Costante `error_codes.{const_name}` non referenziata in "
        f"{_DISCLOSURE_PY.name} — policy decision rolled-back o non migrata."
    )
    assert message_substr in text, (
        f"Messaggio italiano `{message_substr}` mancante in "
        f"{_DISCLOSURE_PY.name} — disclosure regression."
    )


# 2. Per-site code + message lock — ownership.py


_OWNERSHIP_PY_DISCLOSURE_LOCKS: List[Tuple[str, str]] = [
    # ACCESS_DENIED gia' migrato (current contract); ownership.py ora delega
    # via `disclosure.access_denied()`.
    ("access_denied", "Accesso negato"),
    # AUTH_HIERARCHICAL_OWNER_REQUIRED (audit 24, assert_can_modify
    # superadmin-creator branch). Now `disclosure.hierarchical_owner_required(label)`.
    ("hierarchical_owner_required",
     "creata da un superadmin: solo un superadmin può modificarla"),
    # AUTH_OWNER_ONLY (audit 27, _check_ownership not-owner branch).
    # Now `disclosure.owner_only_required(resource_label)`.
    ("owner_only_required", "Permesso negato: solo l'owner del"),
]


@pytest.mark.parametrize(
    "factory_name,message_substr", _OWNERSHIP_PY_DISCLOSURE_LOCKS
)
def test_ownership_py_delegates_to_disclosure_factory(
    factory_name: str, message_substr: str
) -> None:
    """ownership.py must call `disclosure.<factory_name>(...)` (the
    policy lives in `disclosure.py`). The italian message is locked
    at the disclosure module."""
    ownership_text = _read(_OWNERSHIP_PY)
    assert f"_disclosure.{factory_name}" in ownership_text, (
        f"ownership.py must delegate to `_disclosure.{factory_name}(...)`."
    )
    disclosure_text = _read(_DISCLOSURE_PY)
    assert message_substr in disclosure_text, (
        f"Italian message `{message_substr}` missing from "
        f"{_DISCLOSURE_PY.name} — disclosure regression."
    )


# 3. Header preservation — WWW-Authenticate + Retry-After


def test_www_authenticate_bearer_preserved_in_disclosure_module() -> None:
    """The 3 token-related 401 factories must carry
    `WWW-Authenticate: Bearer`. Source-bound: the literal must be in
    `disclosure.py` and reachable by `token_missing` / `token_invalid`
    / `token_revoked`."""
    text = _read(_DISCLOSURE_PY)
    assert '"WWW-Authenticate": "Bearer"' in text, (
        "WWW-Authenticate: Bearer literal missing from disclosure.py."
    )
    # All three factories present.
    for fn_name in ("token_missing", "token_invalid", "token_revoked"):
        assert f"def {fn_name}" in text, (
            f"disclosure.{fn_name} factory missing."
        )


def test_retry_after_header_preserved_in_rate_limit_factory() -> None:
    """The rate-limit 429 factory must emit `Retry-After` alongside
    `auth_rate_limit_exceeded`. Both must live in `disclosure.py`."""
    text = _read(_DISCLOSURE_PY)
    assert '"Retry-After"' in text
    assert "AUTH_RATE_LIMIT_EXCEEDED" in text
    assert "def rate_limit_exceeded" in text


def test_token_missing_factory_uses_typed_helper_with_header() -> None:
    """Architectural invariant: the 401 token-missing factory must
    package `WWW-Authenticate: Bearer` and `AUTH_TOKEN_MISSING` in
    one decision so call sites cannot accidentally drop the header."""
    text = _read(_DISCLOSURE_PY)
    pattern = re.compile(
        r"def token_missing\(\)[^:]*:.*?"
        r"AUTH_TOKEN_MISSING.*?"
        r"WWW-Authenticate.*?Bearer",
        re.DOTALL,
    )
    assert pattern.search(text), (
        "AUTH_TOKEN_MISSING decision must carry "
        "`WWW-Authenticate: Bearer` in `disclosure.token_missing()`."
    )


# 4. Anti-enumeration invariants


def test_invariant_login_bad_creds_uniform_401() -> None:
    """Anti-enum #1: login bad creds usa UNICAMENTE
    `auth_invalid_credentials`, codice unico per "user inesistente"
    e "password errata". Constant-time bcrypt verify obbligatorio.

    The disclosure boundary owns the policy: exactly one factory
    `invalid_credentials()` references AUTH_INVALID_CREDENTIALS, and
    `auth.py` must still apply `_DUMMY_PASSWORD_HASH` for timing
    parity.
    """
    disclosure_text = _read(_DISCLOSURE_PY)
    # Single `def invalid_credentials` in disclosure.py.
    defs = re.findall(
        r"^def\s+invalid_credentials\b", disclosure_text, flags=re.MULTILINE
    )
    assert len(defs) == 1, (
        f"`invalid_credentials` deve essere una sola factory in "
        f"disclosure.py; trovate {len(defs)}. Splittare romperebbe "
        f"anti-enum."
    )
    # Constant-time bcrypt verify still in auth.py.
    auth_text = _read(_AUTH_PY)
    assert "_DUMMY_PASSWORD_HASH" in auth_text, (
        "Constant-time bcrypt verify (`_DUMMY_PASSWORD_HASH`) "
        "obbligatorio per anti-enumeration login."
    )


def test_invariant_post_token_user_gone_uniform_401() -> None:
    """Anti-enum #2: dopo decode_token OK, user inesistente OR
    `is_active=False` ritornano LO STESSO codice via la single
    factory `disclosure.user_not_found_or_disabled()`."""
    text = _read(_DISCLOSURE_PY)
    defs = re.findall(
        r"^def\s+user_not_found_or_disabled\b", text, flags=re.MULTILINE
    )
    assert len(defs) == 1, (
        f"`user_not_found_or_disabled` deve essere una sola factory; "
        f"trovate {len(defs)}. Splittare romperebbe anti-enum."
    )


def test_invariant_admin_reset_delete_uniform_403_access_denied() -> None:
    """Anti-enum #3: admin reset_password / delete_user su target
    fuori-gruppo o inesistente devono emettere ENTRAMBI
    `access_denied` (403). Solo superadmin riceve un 404 distinto.

    The dispatch is encoded in `disclosure.admin_target_not_visible`.
    Il caller (auth.py Previous contract 020, ora `users_router.py` post
    BE-GO-READY-AUTH-ENGINE-003) deve invocare il dispatcher in
    ENTRAMBI reset_password e delete_user — non puo' inline-splittare
    fra USER_NOT_FOUND e ACCESS_DENIED."""
    # Lo scope auth-domain comprende auth.py + users_router.py
    # post-extraction. Le 2 occorrenze del dispatcher si trovano
    # in users_router.py.
    auth_text = _read(_AUTH_PY)
    users_text = _read(_USERS_ROUTER_PY)
    combined = auth_text + "\n" + users_text
    n = combined.count("admin_target_not_visible")
    assert n >= 2, (
        f"auth-domain (auth.py + users_router.py) deve chiamare "
        f"`_disclosure.admin_target_not_visible(...)` in ENTRAMBI "
        f"reset_password e delete_user (anti-enum #3); "
        f"trovate {n} occorrenze."
    )
    # The dispatcher must exist and own both branches.
    disclosure_text = _read(_DISCLOSURE_PY)
    assert "def admin_target_not_visible" in disclosure_text


def test_invariant_account_disabled_distinct_from_invalid_creds() -> None:
    """Anti-enum #4 trade-off (LEAKY_BY_DESIGN, ADR-0018-style):
    account disabled (post-password-ok) emette
    `auth_account_disabled` (403), distinto da `auth_invalid_credentials`
    (401, password errata o user inesistente).
    """
    text = _read(_DISCLOSURE_PY)
    assert "AUTH_ACCOUNT_DISABLED" in text
    # Status code 403 explicit next to the code (not 401).
    pattern = re.compile(
        r"def\s+account_disabled\(\)[^:]*:.*?"
        r"AUTH_ACCOUNT_DISABLED.*?"
        r"status_code\s*=\s*403",
        re.DOTALL,
    )
    assert pattern.search(text), (
        "AUTH_ACCOUNT_DISABLED deve restare a 403 (post-password-ok), "
        "distinto da 401 invalid_credentials, in disclosure.py."
    )


# 5. Catalog completeness lock — 27 codici auth family


_AUTH_FAMILY_CODES = {
    "auth_token_missing",
    "auth_token_invalid",
    "auth_token_revoked",
    "auth_user_not_found_or_disabled",
    "auth_invalid_credentials",
    "auth_mfa_invalid",
    "auth_account_disabled",
    "auth_mfa_required",
    "auth_role_insufficient_for_write",
    "auth_role_kiosk_only",
    "auth_role_admin_required",
    "auth_role_superadmin_required",
    "auth_role_create_privilege_required",
    "auth_field_role_restricted",
    "auth_self_modify_denied",
    "auth_self_disable_denied",
    "auth_hierarchical_owner_required",
    "auth_owner_only",
    "user_not_found",
    "group_not_found",
    "username_already_exists",
    "group_id_already_exists",
    "group_default_not_deletable",
    "auth_mfa_code_invalid",
    "auth_self_delete_denied",
    "auth_rate_limit_exceeded",
    "auth_revocation_unavailable",
}


def test_auth_family_count_twentyseven() -> None:
    assert len(_AUTH_FAMILY_CODES) == 27


def test_auth_family_codes_in_known_domain_codes() -> None:
    missing = _AUTH_FAMILY_CODES - error_codes.KNOWN_DOMAIN_CODES
    assert missing == set(), (
        f"Codici auth-family mancanti dal catalog: {sorted(missing)}"
    )
