"""Contract test BE-GO-READY-AUTH-DOMAIN-PROMOTION-001.

Source-bound: parsa `app/core/auth/auth.py` e `ownership.py` senza
importare i moduli applicativi. Verifica che le promotion previste
dalla ADR-0022 siano in place e che gli anti-enumeration invariants
siano preservati al livello di codice sorgente.

Cinque obiettivi:

1. **Per-resource code lock**: per ogni call site auth-domain
   migrato, verifica che il codice catalog atteso appaia
   (`error_codes.<CONST>`) accanto al messaggio italiano user-facing
   atteso. Drift in piu' = qualcuno ha cambiato un message; drift
   in meno = qualcuno ha rolled-back la migrazione.
2. **WWW-Authenticate header preservation**: i 4 call site 401
   token-related devono preservare `headers={"WWW-Authenticate":
   "Bearer"}` usando il typed error helper. Source-bound: presenza
   del literal.
3. **Retry-After header preservation**: il rate-limit 429 deve
   continuare ad emettere `Retry-After`.
4. **Anti-enumeration invariants** (4): pattern souce-bound che
   bloccano contemporaneamente lo status code, il messaggio e il
   codice catalog di:
   a. login bad-creds — UNIFORM 401 + `auth_invalid_credentials`;
   b. post-token user-not-found-or-disabled — UNIFORM 401 +
      `auth_user_not_found_or_disabled`;
   c. admin reset/delete out-of-group OR not-existing — UNIFORM
      403 + `access_denied`;
   d. account disabled (post-password-ok) — `auth_account_disabled`
      (LEAKY_BY_DESIGN, distinct from invalid_credentials).
5. **Catalog completeness lock**: i 27 nuovi codici sono in
   `KNOWN_DOMAIN_CODES`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

import pytest

from app.shared import error_codes


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_AUTH_PY = _BACKEND_ROOT / "app" / "core" / "auth" / "auth.py"
_OWNERSHIP_PY = _BACKEND_ROOT / "app" / "core" / "auth" / "ownership.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Per-site code lock — auth.py
# ---------------------------------------------------------------------------


# (constant_name, italian_message_substring) per ogni promotion in auth.py.
# Lock minimo source-bound: la costante e il messaggio italiano devono
# apparire entrambi nel sorgente. Non si lockano i numeri di riga (brittle
# al refactor).
_AUTH_PY_LOCKS: List[Tuple[str, str]] = [
    # 401 token-related with WWW-Authenticate header.
    ("AUTH_TOKEN_MISSING", "Token mancante"),
    ("AUTH_TOKEN_INVALID", "Token invalido o scaduto"),
    ("AUTH_TOKEN_REVOKED", "Token revocato"),
    # 401 post-token user-not-found-or-disabled (no WWW-Authenticate,
    # raise_typed_http)
    ("AUTH_USER_NOT_FOUND_OR_DISABLED", "Utente non trovato o disabilitato"),
    # 401 login bad creds
    ("AUTH_INVALID_CREDENTIALS", "Username o password errati"),
    # 401 mfa invalid (post-password-ok, login)
    ("AUTH_MFA_INVALID", "mfa_invalid"),
    # 403 account disabled (post-password-ok)
    ("AUTH_ACCOUNT_DISABLED", "Account disabilitato"),
    # 403 mfa required (post-password-ok)
    ("AUTH_MFA_REQUIRED", "mfa_required"),
    # 403 role-based
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
    # 404 user/group not-found
    ("USER_NOT_FOUND", "Utente non trovato"),
    ("GROUP_NOT_FOUND", "Gruppo non trovato"),
    # 409 conflict
    ("USERNAME_ALREADY_EXISTS", "Username già esistente"),
    ("GROUP_ID_ALREADY_EXISTS", "group_id già esistente"),
    # 400 validation auth-related
    ("GROUP_DEFAULT_NOT_DELETABLE",
     "Il gruppo 'default' non può essere eliminato"),
    ("AUTH_MFA_CODE_INVALID", "Codice MFA non valido"),
    ("AUTH_SELF_DELETE_DENIED", "Non puoi eliminare te stesso"),
    # 429 rate-limit with Retry-After.
    ("AUTH_RATE_LIMIT_EXCEEDED", "Troppi tentativi. Riprova tra"),
    # 503 infra fail-loud (revocation)
    ("AUTH_REVOCATION_UNAVAILABLE",
     "Servizio di revoca token non disponibile, riprova"),
]


@pytest.mark.parametrize("const_name,message_substr", _AUTH_PY_LOCKS)
def test_auth_py_promotion_constant_and_message(
    const_name: str, message_substr: str
) -> None:
    """Per ogni call site di `auth.py` migrato: la costante catalog
    appare e il messaggio italiano user-facing e' preservato."""
    text = _read(_AUTH_PY)
    assert f"error_codes.{const_name}" in text, (
        f"Costante `error_codes.{const_name}` non referenziata in "
        f"{_AUTH_PY.name} — call site rolled-back o non migrato."
    )
    assert message_substr in text, (
        f"Messaggio italiano `{message_substr}` mancante in "
        f"{_AUTH_PY.name} — disclosure regression."
    )


# ---------------------------------------------------------------------------
# 2. Per-site code lock — ownership.py
# ---------------------------------------------------------------------------


_OWNERSHIP_PY_LOCKS: List[Tuple[str, str]] = [
    # ACCESS_DENIED gia' migrato (batch 014), restano locked qui per non
    # regressionare.
    ("ACCESS_DENIED", "Accesso negato"),
    # AUTH_HIERARCHICAL_OWNER_REQUIRED (audit 24, assert_can_modify
    # superadmin-creator branch).
    ("AUTH_HIERARCHICAL_OWNER_REQUIRED",
     "creata da un superadmin: solo un superadmin può modificarla"),
    # AUTH_OWNER_ONLY (audit 27, _check_ownership not-owner branch).
    ("AUTH_OWNER_ONLY",
     "Permesso negato: solo l'owner del"),
]


@pytest.mark.parametrize("const_name,message_substr", _OWNERSHIP_PY_LOCKS)
def test_ownership_py_promotion_constant_and_message(
    const_name: str, message_substr: str
) -> None:
    text = _read(_OWNERSHIP_PY)
    assert f"error_codes.{const_name}" in text, (
        f"Costante `error_codes.{const_name}` non referenziata in "
        f"{_OWNERSHIP_PY.name}."
    )
    assert message_substr in text, (
        f"Messaggio italiano `{message_substr}` mancante in "
        f"{_OWNERSHIP_PY.name}."
    )


# ---------------------------------------------------------------------------
# 3. Header preservation — WWW-Authenticate + Retry-After
# ---------------------------------------------------------------------------


def test_www_authenticate_bearer_preserved_for_all_token_sites() -> None:
    """I 4 call site 401 token-related devono emettere
    `WWW-Authenticate: Bearer`. Source-bound: contiamo le occorrenze
    del literal nel file."""
    text = _read(_AUTH_PY)
    n = text.count('"WWW-Authenticate": "Bearer"')
    assert n >= 4, (
        f"Atteso almeno 4 occorrenze di `WWW-Authenticate: Bearer` "
        f"in auth.py (token missing/invalid/revoked in get_current_user "
        f"+ 2 in logout); trovato {n}."
    )


def test_retry_after_header_preserved_in_rate_limit_site() -> None:
    """Il rate-limit 429 deve emettere `Retry-After` accanto al codice
    `auth_rate_limit_exceeded`. Verifichiamo che lo stesso file
    contenga sia il header che il codice."""
    text = _read(_AUTH_PY)
    assert '"Retry-After"' in text
    assert "AUTH_RATE_LIMIT_EXCEEDED" in text


def test_token_missing_callsite_uses_typed_helper_with_header() -> None:
    """Vincolo architetturale: i 401 token-related che richiedono
    `WWW-Authenticate` devono usare il typed helper con header
    preservato."""
    text = _read(_AUTH_PY)
    # Match: error_codes.AUTH_TOKEN_MISSING accompagnato da
    # "WWW-Authenticate" entro 400 char.
    pattern = re.compile(
        r'error_codes\.AUTH_TOKEN_MISSING.{0,400}WWW-Authenticate',
        re.DOTALL,
    )
    assert pattern.search(text), (
        "AUTH_TOKEN_MISSING deve emettere `WWW-Authenticate: Bearer` "
        "via raise_typed_http(headers=...)."
    )


# ---------------------------------------------------------------------------
# 4. Anti-enumeration invariants
# ---------------------------------------------------------------------------


def test_invariant_login_bad_creds_uniform_401() -> None:
    """Anti-enum #1: login bad creds usa UNICAMENTE
    `auth_invalid_credentials`, codice unico per "user inesistente"
    e "password errata". Constant-time bcrypt verify obbligatorio.
    """
    text = _read(_AUTH_PY)
    # Single emission of AUTH_INVALID_CREDENTIALS (in login()).
    n = text.count("error_codes.AUTH_INVALID_CREDENTIALS")
    assert n == 1, (
        f"AUTH_INVALID_CREDENTIALS deve essere emesso esattamente 1 "
        f"volta (auth.py login bad-creds branch); trovato {n}. "
        "Splittare in piu' codici romperebbe anti-enumeration."
    )
    # Constant-time bcrypt verify: il `_DUMMY_PASSWORD_HASH` deve
    # essere applicato a user inesistente prima del check.
    assert "_DUMMY_PASSWORD_HASH" in text, (
        "Constant-time bcrypt verify (`_DUMMY_PASSWORD_HASH`) "
        "obbligatorio per anti-enumeration login."
    )


def test_invariant_post_token_user_gone_uniform_401() -> None:
    """Anti-enum #2: dopo decode_token OK, user inesistente OR
    `is_active=False` ritornano LO STESSO codice
    `auth_user_not_found_or_disabled` con LO STESSO messaggio
    italiano. NON splittare."""
    text = _read(_AUTH_PY)
    n = text.count("error_codes.AUTH_USER_NOT_FOUND_OR_DISABLED")
    assert n == 1, (
        f"AUTH_USER_NOT_FOUND_OR_DISABLED deve essere emesso "
        f"esattamente 1 volta; trovato {n}. Splittare in due codici "
        f"distinti per i due rami romperebbe anti-enumeration."
    )


def test_invariant_admin_reset_delete_uniform_403_access_denied() -> None:
    """Anti-enum #3: admin reset_password / delete_user su target
    fuori-gruppo o inesistente devono emettere ENTRAMBI
    `access_denied` (403, batch 014). Solo superadmin riceve un 404
    distinto (privilegio inerente: vede tutto).

    Source-bound: i due endpoint devono sia emettere
    `error_codes.ACCESS_DENIED` (per il path admin non-superadmin)
    sia `error_codes.USER_NOT_FOUND` (per il path superadmin).
    """
    text = _read(_AUTH_PY)
    assert "error_codes.ACCESS_DENIED" in text, (
        "auth.py deve continuare a usare ACCESS_DENIED nei path "
        "anti-enum per admin reset/delete (batch 014, locked)."
    )
    # Verifica entrambi i path (admin uniform 403 + superadmin 404).
    # Cerchiamo `error_codes.USER_NOT_FOUND` in funzione almeno una volta.
    assert "error_codes.USER_NOT_FOUND" in text, (
        "auth.py deve emettere USER_NOT_FOUND nel path superadmin di "
        "reset_password/delete_user (e in /me, change_password, ecc.)."
    )


def test_invariant_account_disabled_distinct_from_invalid_creds() -> None:
    """Anti-enum #4 trade-off (LEAKY_BY_DESIGN, ADR-0018-style):
    account disabled (post-password-ok) emette
    `auth_account_disabled` (403), distinto da `auth_invalid_credentials`
    (401, password errata o user inesistente).

    Questa distinzione e' VOLUTA: l'amministratore deve sapere se
    l'account e' attivo. Trade-off rate-limited per IP.
    """
    text = _read(_AUTH_PY)
    assert "error_codes.AUTH_ACCOUNT_DISABLED" in text
    # Status code 403 esplicito accanto al codice (non 401).
    pattern = re.compile(
        r'error_codes\.AUTH_ACCOUNT_DISABLED.{0,200}status_code\s*=\s*403',
        re.DOTALL,
    )
    assert pattern.search(text), (
        "AUTH_ACCOUNT_DISABLED deve restare a 403 (post-password-ok), "
        "distinto da 401 invalid_credentials."
    )


# ---------------------------------------------------------------------------
# 5. Catalog completeness lock
# ---------------------------------------------------------------------------


# Atteso: 27 nuovi codici dalla famiglia auth, da BE-GO-READY-AUTH-
# DOMAIN-PROMOTION-001. Lock di chiusura per drift "in meno"
# (qualcuno rimuove un codice del catalog senza aggiornare i call site).
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
    """Tutti i 27 codici della famiglia auth sono in
    `KNOWN_DOMAIN_CODES`."""
    missing = _AUTH_FAMILY_CODES - error_codes.KNOWN_DOMAIN_CODES
    assert missing == set(), (
        f"Codici auth-family mancanti dal catalog: {sorted(missing)}"
    )
