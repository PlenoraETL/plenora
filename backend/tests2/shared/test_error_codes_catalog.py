"""
Contract test BE-GO-READY-ERROR-CODES-006-CATALOG —

catalog `error_codes.py` + helper `raise_typed_http` + drift
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict, Set

import pytest
from fastapi import HTTPException

from app.core.admin_backups import errors as admin_backup_errors
from app.core.connections import errors as connection_errors
from app.core.datasources import errors as datasource_errors
from app.core.tickets import errors as ticket_errors
from app.shared import error_codes
from app.shared.error_envelope import (
    normalize_http_exception_detail,
    raise_typed_http,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent  # .../backend
_CORE_ROOT = _BACKEND_ROOT / "app" / "core"


# 1. Catalog membership locks


# Lock atteso del catalog domain (BE-GO-READY-ERROR-CODES-006).
# Drift in piu' = qualcuno ha aggiunto una costante senza
# aggiornare la lista; drift in meno = costante rimossa
# accidentalmente.
EXPECTED_DOMAIN_CODES: Set[str] = {
    # batch 006
    "connection_in_use",
    "connection_duplicate_name",
    "api_connection_in_use",
    "project_has_dependencies",
    "project_cleanup_partial",
    "datasource_in_use",
    "object_storage_disabled",
    "aws_crt_missing",
    "aws_credentials_expired",
    "carica_launch_failed",
    "carica_scheduler_failed",
    "schedule_execution_fatal",
    # batch 009 — Not-Found family
    "project_not_found",
    "task_not_found",
    "schedule_not_found",
    "run_not_found",
    "bundle_not_found",
    "connection_not_found",
    "api_connection_not_found",
    "file_source_not_found",
    "datasource_not_found",
    "ticket_not_found",
    # batch 010 — External-Service family
    "external_service_unreachable",
    # BE-GO-READY-MEMORY-OOM-001 — Memory/OOM family
    "memory_limit_exceeded",
    # BE-GO-READY-ERROR-CODES-013 — Service initialization family
    "service_not_initialized",
    # BE-GO-READY-ERROR-CODES-014 — Access Denied family
    "access_denied",
    # BE-GO-READY-AUTH-DOMAIN-PROMOTION-001 — Auth family
    # 401 — autenticazione
    "auth_token_missing",
    "auth_token_invalid",
    "auth_token_revoked",
    "auth_user_not_found_or_disabled",
    "auth_invalid_credentials",
    "auth_mfa_invalid",
    # 403 — autorizzazione
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
    # 404 — after-auth resource not found
    "user_not_found",
    "group_not_found",
    # 409 — conflict
    "username_already_exists",
    "group_id_already_exists",
    # 400 — validation auth-related
    "group_default_not_deletable",
    "auth_mfa_code_invalid",
    "auth_self_delete_denied",
    # 429 — rate-limit
    "auth_rate_limit_exceeded",
    # 503 — infra fail-loud
    "auth_revocation_unavailable",
}

# Pattern accettati come `http_<status>` fallback intenzionale
# (BE-GO-READY-ERROR-CODES-009-NOT-FOUND-PROMOTION).
# Lockati per evitare che il drift detection li riconti come
# residui da promuovere.
EXPECTED_FALLBACK_PATTERNS: Set[str] = {
    # "internal_error_generic" promoted to raise_typed_http(INTERNAL_ERROR)
    # in BE-GO-READY-ERROR-CODES-011.
    # "service_init_guard" promoted to raise_typed_http(SERVICE_NOT_INITIALIZED)
    # in BE-GO-READY-ERROR-CODES-013.
    # "generic_access_denied" promoted to raise_typed_http(ACCESS_DENIED)
    # in BE-GO-READY-ERROR-CODES-014.
    "value_error_str_e",
}

EXPECTED_GLOBAL_CODES: Set[str] = {
    "internal_error",
    "canvas_corrupted",
    "validation_error",
}

_GLOBAL_CONSTANT_NAMES = {
    "INTERNAL_ERROR",
    "CANVAS_CORRUPTED",
    "VALIDATION_ERROR",
}

_MIGRATED_DOMAIN_MODULES = {
    "app/core/admin_backups/router.py": (
        "admin_backup_errors",
        {
            "OBJECT_STORAGE_DISABLED",
            "AWS_CRT_MISSING",
            "AWS_CREDENTIALS_EXPIRED",
        },
    ),
    "app/core/connections/router.py": (
        "connection_errors",
        {
            "CONNECTION_IN_USE",
            "CONNECTION_NOT_FOUND",
            "CONNECTION_DUPLICATE_NAME",
        },
    ),
    "app/core/carica/router.py": (
        "connection_errors",
        {
            "CONNECTION_NOT_FOUND",
        },
    ),
    "app/core/datasources/router.py": (
        "datasource_errors",
        {
            "DATASOURCE_IN_USE",
            "DATASOURCE_NOT_FOUND",
        },
    ),
    "app/core/tickets/service.py": (
        "ticket_errors",
        {
            "TICKET_NOT_FOUND",
        },
    ),
}

_MIGRATED_DOMAIN_CONSTANTS = {
    const
    for _, constants in _MIGRATED_DOMAIN_MODULES.values()
    for const in constants
}


def test_known_domain_codes_matches_expected_lock():
    """Il `frozenset` esposto dal catalog deve combaciare
    esattamente con il set atteso del batch 006."""
    assert error_codes.KNOWN_DOMAIN_CODES == EXPECTED_DOMAIN_CODES


def test_known_global_codes_matches_expected_lock():
    assert error_codes.KNOWN_GLOBAL_CODES == EXPECTED_GLOBAL_CODES


def test_catalog_count_domain_fiftythree():
    """53 codici domain (5 pre-existing + 6 batch 006 + 10 batch 009
    not-found family + 1 batch 010 external-service +
    1 BE-GO-READY-MEMORY-OOM-001 memory_limit_exceeded +
    1 BE-GO-READY-ERROR-CODES-013 service_not_initialized +
    1 BE-GO-READY-ERROR-CODES-014 access_denied +
    27 BE-GO-READY-AUTH-DOMAIN-PROMOTION-001 auth family +
    1 compatibility domain conflict code)."""
    assert len(EXPECTED_DOMAIN_CODES) == 53
    assert len(error_codes.KNOWN_DOMAIN_CODES) == len(EXPECTED_DOMAIN_CODES)


def test_catalog_domain_and_global_disjoint():
    """Nessun codice puo' essere sia domain che globale (semantiche
    diverse: globale = envelope-level, domain = business-level)."""
    overlap = error_codes.KNOWN_DOMAIN_CODES & error_codes.KNOWN_GLOBAL_CODES
    assert overlap == set(), f"Overlap inatteso: {overlap}"


def test_catalog_constants_match_shared_owned_set_values():
    """Shared-owned constants must match the aggregate domain catalog."""
    domain_constants = {
        value
        for name, value in vars(error_codes).items()
        if (
            name.isupper()
            and name not in _GLOBAL_CONSTANT_NAMES
            and isinstance(value, str)
        )
    }
    assert domain_constants == set(error_codes.KNOWN_DOMAIN_CODES)


def test_migrated_domain_modules_feed_shared_catalog():
    assert admin_backup_errors.KNOWN_DOMAIN_CODES <= error_codes.KNOWN_DOMAIN_CODES
    assert connection_errors.KNOWN_DOMAIN_CODES <= error_codes.KNOWN_DOMAIN_CODES
    assert datasource_errors.KNOWN_DOMAIN_CODES <= error_codes.KNOWN_DOMAIN_CODES
    assert ticket_errors.KNOWN_DOMAIN_CODES <= error_codes.KNOWN_DOMAIN_CODES


def test_migrated_domain_modules_expose_their_compat_catalogs():
    assert admin_backup_errors.KNOWN_DOMAIN_CODES == {
        error_codes.OBJECT_STORAGE_DISABLED,
        error_codes.AWS_CRT_MISSING,
        error_codes.AWS_CREDENTIALS_EXPIRED,
    }
    assert connection_errors.KNOWN_DOMAIN_CODES == {
        error_codes.CONNECTION_IN_USE,
        error_codes.CONNECTION_NOT_FOUND,
        error_codes.CONNECTION_DUPLICATE_NAME,
    }
    assert datasource_errors.KNOWN_DOMAIN_CODES == {
        error_codes.DATASOURCE_IN_USE,
        error_codes.DATASOURCE_NOT_FOUND,
    }
    assert ticket_errors.KNOWN_DOMAIN_CODES == {error_codes.TICKET_NOT_FOUND}


def test_migrated_domain_codes_are_canonical_in_shared_catalog():
    for const in _MIGRATED_DOMAIN_CONSTANTS:
        assert getattr(error_codes, const) in error_codes.KNOWN_DOMAIN_CODES


def test_migrated_domain_codes_are_defined_in_shared_catalog():
    source = (_BACKEND_ROOT / "app" / "shared" / "error_codes.py").read_text(
        encoding="utf-8",
    )
    centrally_defined = {
        const
        for const in _MIGRATED_DOMAIN_CONSTANTS
        if re.search(rf"^{const}\s*=", source, re.MULTILINE)
    }
    assert centrally_defined == _MIGRATED_DOMAIN_CONSTANTS


def test_migrated_domain_call_sites_use_domain_error_modules():
    violations = []
    missing_refs = []
    for rel_path, (alias, constants) in _MIGRATED_DOMAIN_MODULES.items():
        text = (_BACKEND_ROOT / rel_path).read_text(encoding="utf-8")
        for const in constants:
            if f"error_codes.{const}" in text:
                violations.append(f"{rel_path}:error_codes.{const}")
            if f"{alias}.{const}" not in text:
                missing_refs.append(f"{rel_path}:{alias}.{const}")

    assert violations == [], (
        "Migrated domain call sites must not use app.shared.error_codes "
        f"for owned constants: {violations}"
    )
    assert missing_refs == [], (
        "Migrated domain call sites must import/use their domain errors "
        f"module: {missing_refs}"
    )


def test_accepted_http_fallback_patterns_lock():
    """Lock i pattern accettati come fallback intenzionale
    (BE-GO-READY-ERROR-CODES-009). Riduce il drift detection
    paranoico sui ~165 string-detail intenzionalmente lasciati
    come `http_<status>` (vedi audit 008)."""
    assert error_codes.ACCEPTED_HTTP_FALLBACK_PATTERNS == EXPECTED_FALLBACK_PATTERNS


def test_accepted_http_fallback_patterns_disjoint_from_codes():
    """I pattern fallback NON sono codici emettibili: vivono in un
    set separato. Drift fail = qualcuno ha confuso un pattern con
    un codice domain."""
    overlap = (
        error_codes.ACCEPTED_HTTP_FALLBACK_PATTERNS
        & (error_codes.KNOWN_DOMAIN_CODES | error_codes.KNOWN_GLOBAL_CODES)
    )
    assert overlap == set(), f"Overlap pattern/codici: {overlap}"


def test_not_found_family_uses_consistent_suffix():
    """Tutti i 10 codici not-found family hanno suffisso `_not_found`."""
    not_found_codes = {
        error_codes.PROJECT_NOT_FOUND,
        error_codes.TASK_NOT_FOUND,
        error_codes.SCHEDULE_NOT_FOUND,
        error_codes.RUN_NOT_FOUND,
        error_codes.BUNDLE_NOT_FOUND,
        connection_errors.CONNECTION_NOT_FOUND,
        error_codes.API_CONNECTION_NOT_FOUND,
        error_codes.FILE_SOURCE_NOT_FOUND,
        datasource_errors.DATASOURCE_NOT_FOUND,
        ticket_errors.TICKET_NOT_FOUND,
    }
    for code in not_found_codes:
        assert code.endswith("_not_found"), (
            f"Codice not-found `{code}` non rispetta convenzione suffisso."
        )


# 2. Convenzioni codici


_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")


def test_codes_are_snake_case():
    """snake_case lock per stabilita' API: nessun camelCase,
    PascalCase, dash-case, prefisso/suffisso underscore."""
    for code in error_codes.KNOWN_DOMAIN_CODES | error_codes.KNOWN_GLOBAL_CODES:
        assert _SNAKE_CASE_RE.match(code), (
            f"Codice `{code}` non e' snake_case canonico."
        )


def test_codes_are_unique():
    """Verifica implicita di non-collision: convertire a set non
    deve perdere elementi rispetto alla dichiarazione esplicita."""
    declared_strings = [
        "connection_in_use",
        "api_connection_in_use",
        "project_has_dependencies",
        "project_cleanup_partial",
        "datasource_in_use",
        "object_storage_disabled",
        "aws_crt_missing",
        "aws_credentials_expired",
        "carica_launch_failed",
        "carica_scheduler_failed",
        "schedule_execution_fatal",
        "internal_error",
        "canvas_corrupted",
        "validation_error",
    ]
    assert len(declared_strings) == len(set(declared_strings))


def test_http_status_code_factory_produces_http_prefix():
    """Codice generico `http_<status>` per fallback string-detail."""
    assert error_codes.http_status_code(404) == "http_404"
    assert error_codes.http_status_code(500) == "http_500"
    assert error_codes.http_status_code(503) == "http_503"


# 3. raise_typed_http helper — shape + round-trip envelope


def test_raise_typed_http_raises_http_exception():
    with pytest.raises(HTTPException) as exc_info:
        raise_typed_http(
            code="connection_in_use",
            status_code=409,
            message="Connessione referenziata.",
        )
    assert exc_info.value.status_code == 409


def test_raise_typed_http_detail_is_dict_with_error_message():
    with pytest.raises(HTTPException) as exc_info:
        raise_typed_http(
            code="connection_in_use",
            status_code=409,
            message="Connessione referenziata.",
        )
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["error"] == "connection_in_use"
    assert detail["message"] == "Connessione referenziata."


def test_raise_typed_http_propagates_extras_into_detail():
    """`details` extras (es. `dependencies`) confluiscono accanto a
    `error`/`message` nel dict, allineato al pattern dei call site
    esistenti che il central handler sa normalizzare."""
    with pytest.raises(HTTPException) as exc_info:
        raise_typed_http(
            code="connection_in_use",
            status_code=409,
            message="msg",
            details={"dependencies": [{"id": 1}, {"id": 2}]},
        )
    detail = exc_info.value.detail
    assert detail["details"] == {"dependencies": [{"id": 1}, {"id": 2}]}


def test_raise_typed_http_does_not_allow_overriding_protected_keys():
    """Caller buggato che mette `error`/`message` nei `details`
    NON deve poter sovrascrivere il contratto del codice."""
    with pytest.raises(HTTPException) as exc_info:
        raise_typed_http(
            code="connection_in_use",
            status_code=409,
            message="canonico",
            details={"error": "MALICIOUS", "message": "MALICIOUS", "x": 1},
        )
    detail = exc_info.value.detail
    assert detail["error"] == "connection_in_use"
    assert detail["message"] == "canonico"
    assert detail["details"] == {"x": 1}


def test_raise_typed_http_no_details_yields_minimal_dict():
    with pytest.raises(HTTPException) as exc_info:
        raise_typed_http(
            code="canvas_corrupted",
            status_code=500,
            message="m",
        )
    detail = exc_info.value.detail
    assert detail == {"error": "canvas_corrupted", "message": "m", "details": {}}


def test_raise_typed_http_message_required_non_empty():
    with pytest.raises(ValueError, match="message must be non-empty"):
        raise_typed_http(
            code="connection_in_use",
            status_code=409,
            message="",
        )


def test_raise_typed_http_round_trip_via_normalizer():
    """Round-trip: l'`HTTPException.detail` emesso da
    `raise_typed_http` deve essere normalizzato dal central
    `normalize_http_exception_detail` nell'envelope canonico
    (codice e message preservati; extras → `details`)."""
    with pytest.raises(HTTPException) as exc_info:
        raise_typed_http(
            code="connection_in_use",
            status_code=409,
            message="Connessione referenziata.",
            details={"dependencies": [{"id": 1}]},
        )
    norm = normalize_http_exception_detail(
        exc_info.value.status_code, exc_info.value.detail,
    )
    assert norm["error"] == "connection_in_use"
    assert norm["message"] == "Connessione referenziata."
    assert norm["details"] == {"dependencies": [{"id": 1}]}


def test_raise_typed_http_works_with_catalog_constant():
    """Adoption path: `raise_typed_http(error_codes.<CONST>, ...)`
    e' la forma raccomandata. Verifica che l'helper accetti i
    valori del catalog senza ulteriore conversione."""
    with pytest.raises(HTTPException) as exc_info:
        raise_typed_http(
            code=admin_backup_errors.OBJECT_STORAGE_DISABLED,
            status_code=503,
            message="Object storage non configurato.",
        )
    assert exc_info.value.detail["error"] == "object_storage_disabled"


# 4. Source-bound drift detection: app/core/**/*.py


_ERROR_LITERAL_RE = re.compile(
    r'["\']error["\']\s*:\s*["\']([a-z_][a-z0-9_]*)["\']'
)


def _scan_router_error_literals() -> Set[str]:
    """Source-bound: parsa tutti i file .py sotto `app/core/`
    cercando occorrenze di `"error": "<snake_case>"` literal.

    Ignora file binari/non-py e file di test (anche se
    `app/core/` non ha test annidati). NON importa moduli
    applicativi (zero side effects).
    """
    found: Set[str] = set()
    for py_file in _CORE_ROOT.rglob("*.py"):
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in _ERROR_LITERAL_RE.finditer(text):
            code = match.group(1)
            # Filtra i codici globali generati dinamicamente
            # (es. `http_404`); tutti i literal `http_<n>` sono
            # match accettati senza essere nel catalog (vivono
            # via `http_status_code(...)` factory).
            if code.startswith("http_") and code[5:].isdigit():
                continue
            found.add(code)
    return found


def test_router_error_literals_are_in_catalog():
    """Drift detection: ogni ``"error": "<x>"`` literal nei file
    `app/core/**/*.py` deve essere classificato come codice
    domain (in `KNOWN_DOMAIN_CODES`) o globale (in
    `KNOWN_GLOBAL_CODES`).

    Drift in meno = qualcuno ha rimosso un codice in uso senza
    aggiornare i call site → trovati codici fuori catalog.
    Drift in piu' (codice in catalog ma non in nessun router)
    NON e' fail qui: i codici globali e alcuni domain sono
    invocati via helper/handler, non come literal.
    """
    found_in_routers = _scan_router_error_literals()
    classified = error_codes.KNOWN_DOMAIN_CODES | error_codes.KNOWN_GLOBAL_CODES
    unclassified = found_in_routers - classified

    assert unclassified == set(), (
        f"Codici literal nei router non classificati: "
        f"{sorted(unclassified)}. Aggiungili a "
        f"`app/shared/error_codes.py` (`KNOWN_DOMAIN_CODES`) o "
        f"documenta perche' devono restare fuori catalog."
    )


_CONSTANT_REF_RE = re.compile(
    r"\berror_codes\.([A-Z][A-Z0-9_]*)\b"
)


def _scan_router_constant_refs() -> Set[str]:
    """Source-bound: parsa `app/core/**/*.py` cercando referenze
    `error_codes.<CONST>` (post-migration BE-GO-READY-ERROR-CODES-007).

    Speculare a `_scan_router_error_literals` ma cattura il pattern
    a costante invece di literal. NESSUN import dei moduli
    applicativi.
    """
    found: Set[str] = set()
    for py_file in _CORE_ROOT.rglob("*.py"):
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in _CONSTANT_REF_RE.finditer(text):
            found.add(match.group(1))
    return found


def test_router_constant_refs_find_baseline():
    """Sanity check for unmigrated `error_codes.<CONST>` call sites.

    Pre-migrazione (audit 005) questa stessa sanity scansionava
    i `"error": "<x>"` literal; post-migrazione i literal sono
    spariti dai router (sostituiti dalle costanti). I codici gia'
    spostati in `app/core/<domain>/errors.py` sono coperti da
    `test_migrated_domain_call_sites_use_domain_error_modules`.

    Fail = qualcuno ha rimosso una costante senza spostare il
    call site, o il regex parser e' rotto.
    """
    found = _scan_router_constant_refs()
    baseline_constants_must_appear = {
        # batch 007 (in-use family)
        "API_CONNECTION_IN_USE",
        "PROJECT_HAS_DEPENDENCIES",
        "PROJECT_CLEANUP_PARTIAL",
        "CARICA_LAUNCH_FAILED",
        # batch 009 (not-found family)
        "PROJECT_NOT_FOUND",
        "TASK_NOT_FOUND",
        "SCHEDULE_NOT_FOUND",
        "RUN_NOT_FOUND",
        "API_CONNECTION_NOT_FOUND",
        "FILE_SOURCE_NOT_FOUND",
        # batch 010 (external-service family)
        "EXTERNAL_SERVICE_UNREACHABLE",
        # batch 011 (global fallback promotion)
        "INTERNAL_ERROR",
        # batch 013 (service init guard promotion)
        "SERVICE_NOT_INITIALIZED",
        # batch 014 (generic access denied promotion)
        "ACCESS_DENIED",
        # BE-GO-READY-AUTH-DOMAIN-PROMOTION-001 (auth family promotion)
        # Tutte queste costanti devono apparire come `error_codes.<CONST>`
        # nei file `app/core/auth/auth.py` o `app/core/auth/ownership.py`
        # post-migrazione. Se manca, vuol dire che un call site e' stato
        # rolled-back o non migrato.
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
        "AUTH_HIERARCHICAL_OWNER_REQUIRED",
        "AUTH_OWNER_ONLY",
        "USER_NOT_FOUND",
        "GROUP_NOT_FOUND",
        "USERNAME_ALREADY_EXISTS",
        "GROUP_ID_ALREADY_EXISTS",
        "GROUP_DEFAULT_NOT_DELETABLE",
        "AUTH_MFA_CODE_INVALID",
        "AUTH_SELF_DELETE_DENIED",
        "AUTH_RATE_LIMIT_EXCEEDED",
        "AUTH_REVOCATION_UNAVAILABLE",
    }
    missing = baseline_constants_must_appear - found
    assert missing == set(), (
        f"Baseline costanti `error_codes.<CONST>` non referenziate "
        f"nei router: {sorted(missing)}. Probabile call site "
        f"rimosso/rolled-back o regex parser rotto."
    )


def test_no_generic_500_string_detail_in_non_auth_routers():
    """Source-bound: nessun file non-auth sotto `app/core/` deve
    contenere old-pattern generic-500 con messaggio
    `"Errore interno del server"`. Tutti promossi a
    `raise_typed_http(error_codes.INTERNAL_ERROR, ...)` in
    BE-GO-READY-ERROR-CODES-011."""
    old_pattern = re.compile(
        r'raise\s+HTTPException\('
        r'(?=[^)]*(?:status_code\s*=\s*)?500)'
        r'(?=[^)]*["\']Errore interno del server["\'])',
        re.DOTALL,
    )
    violations = []
    for py_file in _CORE_ROOT.rglob("*.py"):
        if "auth" in py_file.parts:
            continue
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in old_pattern.finditer(text):
            violations.append(f"{py_file.relative_to(_BACKEND_ROOT)}:{text[:match.start()].count(chr(10))+1}")
    assert violations == [], (
        f"Old generic-500 pattern found (should use raise_typed_http): {violations}"
    )


def test_no_generic_500_string_detail_in_auth_routers():
    """Source-bound: nessun file sotto `app/core/auth/` deve contenere
    old-pattern generic-500 con messaggio `"Errore interno del server"`.
    Promosso a `raise_typed_http(error_codes.INTERNAL_ERROR, ...)` in
    BE-GO-READY-AUTH-ERROR-CODES-001."""
    old_pattern = re.compile(
        r'raise\s+HTTPException\('
        r'(?=[^)]*(?:status_code\s*=\s*)?500)'
        r'(?=[^)]*["\']Errore interno del server["\'])',
        re.DOTALL,
    )
    auth_root = _CORE_ROOT / "auth"
    violations = []
    for py_file in auth_root.rglob("*.py"):
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in old_pattern.finditer(text):
            violations.append(f"{py_file.relative_to(_BACKEND_ROOT)}:{text[:match.start()].count(chr(10))+1}")
    assert violations == [], (
        f"Old generic-500 pattern found in auth (should use raise_typed_http): {violations}"
    )


def test_no_generic_access_denied_string_detail_in_app():
    """Source-bound: nessun file sotto `app/` deve contenere old-pattern
    `raise HTTPException(403, "Accesso negato")` con `detail` stringa.
    Tutti i 12 call site (auth/ownership helpers, auth user-management,
    logs runs router, files router, orchestrator download) sono stati
    promossi a `raise_typed_http(error_codes.ACCESS_DENIED, ...)` in
    BE-GO-READY-ERROR-CODES-014."""
    app_root = _BACKEND_ROOT / "app"
    old_pattern = re.compile(
        r'raise\s+HTTPException\('
        r'(?=[^)]*(?:status_code\s*=\s*)?403)'
        r'(?=[^)]*["\']Accesso negato["\'])',
        re.DOTALL,
    )
    violations = []
    for py_file in app_root.rglob("*.py"):
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in old_pattern.finditer(text):
            violations.append(
                f"{py_file.relative_to(_BACKEND_ROOT)}:"
                f"{text[:match.start()].count(chr(10)) + 1}"
            )
    assert violations == [], (
        f"Old generic_access_denied pattern found (should use raise_typed_http "
        f"with ACCESS_DENIED): {violations}"
    )


def test_generic_access_denied_not_in_accepted_fallback():
    """Lock di chiusura: `generic_access_denied` NON deve piu' figurare
    fra i pattern accettati in `ACCEPTED_HTTP_FALLBACK_PATTERNS`. La
    promozione a `ACCESS_DENIED` (BE-GO-READY-ERROR-CODES-014) ha
    chiuso tutti i 12 call site; il pattern non e' piu' un fallback
    legittimo ma un residuo da prevenire."""
    assert "generic_access_denied" not in error_codes.ACCEPTED_HTTP_FALLBACK_PATTERNS


def test_access_denied_constant_value_lock():
    """Lock del valore stringa: il codice `access_denied` (snake_case)
    e' parte dell'API contract verso il FE. Modifiche del valore
    rompono i consumer; questo test impedisce drift accidentale."""
    assert error_codes.ACCESS_DENIED == "access_denied"


def test_no_service_init_guard_string_detail_in_app():
    """Source-bound: nessun file sotto `app/` deve contenere old-pattern
    `raise HTTPException(500|503, "... non inizializzato")` con
    `detail` stringa. Tutti i call site sono stati promossi a
    `raise_typed_http(error_codes.SERVICE_NOT_INITIALIZED, ...)` in
    BE-GO-READY-ERROR-CODES-013."""
    app_root = _BACKEND_ROOT / "app"
    old_pattern = re.compile(
        r'raise\s+HTTPException\('
        r'(?=[^)]*(?:status_code\s*=\s*)?(?:500|503))'
        r'(?=[^)]*["\'][^"\']*non inizializzato[^"\']*["\'])',
        re.DOTALL,
    )
    violations = []
    for py_file in app_root.rglob("*.py"):
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in old_pattern.finditer(text):
            violations.append(
                f"{py_file.relative_to(_BACKEND_ROOT)}:"
                f"{text[:match.start()].count(chr(10)) + 1}"
            )
    assert violations == [], (
        f"Old service_init_guard pattern found (should use raise_typed_http "
        f"with SERVICE_NOT_INITIALIZED): {violations}"
    )


def _is_str_call_or_sanitized(node: ast.AST) -> bool:
    """Match `str(<ident>)` or `sanitize_http_detail(str(<ident>))`."""
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name) and node.func.id == "str" and node.args:
        return True
    if (
        isinstance(node.func, ast.Name)
        and node.func.id == "sanitize_http_detail"
        and node.args
        and _is_str_call_or_sanitized(node.args[0])
    ):
        return True
    return False


def _http_exception_detail(node: ast.Call) -> ast.AST | None:
    """Extract the `detail` arg from an HTTPException(...) call (positional
    or keyword), or `None` if absent."""
    if len(node.args) >= 2:
        return node.args[1]
    for kw in node.keywords:
        if kw.arg == "detail":
            return kw.value
    return None


def _scan_value_error_str_e_callsites() -> Dict[str, int]:
    """Source-bound AST scan: count `HTTPException(detail=str(<ident>))`
    or `HTTPException(detail=sanitize_http_detail(str(<ident>)))` per
    file under `app/`. Skips files with `SyntaxError` (none expected).

    Excludes calls where `detail` is a dict literal (i.e. structured-
    detail with explicit `error` code — those are NOT `value_error_str_e`
    pattern even when they include `str(e)` as the message field).
    """
    app_root = _BACKEND_ROOT / "app"
    counts: Dict[str, int] = {}
    for py_file in app_root.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not (isinstance(f, ast.Name) and f.id == "HTTPException"):
                continue
            d = _http_exception_detail(node)
            if d is None:
                continue
            if _is_str_call_or_sanitized(d):
                rel = str(py_file.relative_to(_BACKEND_ROOT)).replace("\\", "/")
                counts[rel] = counts.get(rel, 0) + 1
    return counts


# Lock per-file (BE-GO-READY-ERROR-CODES-015 closure).
# 42 call site totali in 11 file, classificati `KEEP_DYNAMIC_VALIDATION_COMPAT`
# (vedi rationale in `error_codes.py::ACCEPTED_HTTP_FALLBACK_PATTERNS`).
# Aggiunte/rimozioni richiedono aggiornamento esplicito del lock e
# classificazione del nuovo sito (KEEP_DYNAMIC_VALIDATION_COMPAT,
# PROMOTE_DOMAIN_CODE, NEEDS_CONTEXT). Drift detection per-file evita
# espansione silente del pattern fra release.
EXPECTED_VALUE_ERROR_STR_E_COUNTS: Dict[str, int] = {
    "backend/app/shared/error_envelope.py": 1,
    "backend/app/core/arricchisci/router.py": 2,
    "backend/app/core/carica/conflict_request_loader.py": 1,
    "backend/app/core/datasources/router.py": 5,
    "backend/app/core/eseguibili/router.py": 4,
    "backend/app/core/estrai/router.py": 1,
    "backend/app/core/features/routes.py": 9,
    "backend/app/core/files/router.py": 7,
    "backend/app/core/files/upload_boundary.py": 2,
    "backend/app/core/orchestrator/router.py": 2,
    "backend/app/core/projects/router.py": 1,
    "backend/app/core/tickets/attachment_boundary.py": 1,
}


def test_value_error_str_e_callsite_lock():
    """Lock per-file dei `HTTPException(detail=str(e))` /
    `HTTPException(detail=sanitize_http_detail(str(e)))` residui.

    Pattern accettato come `value_error_str_e` in
    `ACCEPTED_HTTP_FALLBACK_PATTERNS` (closure
    BE-GO-READY-ERROR-CODES-015). Il lock per-file blocca:
    - drift in piu' (qualcuno aggiunge un nuovo `str(e)` fallback
      senza classificarlo) → forza classificazione esplicita;
    - drift in meno (qualcuno migra un site a domain code senza
      aggiornare il lock) → forza aggiornamento di
      `EXPECTED_VALUE_ERROR_STR_E_COUNTS` per riflettere la nuova
      baseline.

    Non lockiamo line numbers (brittle al refactor); lockiamo
    file -> count.
    """
    found = _scan_value_error_str_e_callsites()
    # Normalize to backend-relative form for comparison.
    normalized = {f"backend/{k}": v for k, v in found.items()}
    assert normalized == EXPECTED_VALUE_ERROR_STR_E_COUNTS, (
        f"Drift `value_error_str_e` rilevato.\n"
        f"Atteso: {EXPECTED_VALUE_ERROR_STR_E_COUNTS}\n"
        f"Trovato: {normalized}\n"
        f"Aggiungi/migra il sito e aggiorna il lock con la nuova "
        f"classificazione (KEEP_DYNAMIC_VALIDATION_COMPAT, "
        f"PROMOTE_DOMAIN_CODE, NEEDS_CONTEXT)."
    )


def test_value_error_str_e_total_count_lock():
    """Sanity check: somma per-file == 42. Lock indipendente che
    cattura desync fra `EXPECTED_VALUE_ERROR_STR_E_COUNTS` e somma
    intesa nel commit message."""
    assert sum(EXPECTED_VALUE_ERROR_STR_E_COUNTS.values()) == 36


def test_value_error_str_e_pattern_in_accepted_fallback():
    """Lock di chiusura: `value_error_str_e` deve restare in
    `ACCEPTED_HTTP_FALLBACK_PATTERNS` finche' la classificazione
    BE-GO-READY-ERROR-CODES-015 e' attiva. Rimozione richiede una
    closure batch successiva (es. promotion mirata di un sottoinsieme
    a `CREDENTIAL_UNAVAILABLE` / `DATA_SOURCE_*`)."""
    assert "value_error_str_e" in error_codes.ACCEPTED_HTTP_FALLBACK_PATTERNS


def test_drift_scan_ignores_http_status_dynamic_codes():
    """`http_<status>` non deve entrare nel set domain: e' un
    codice generato dinamicamente da
    `error_codes.http_status_code(...)`, non un literal in catalog."""
    found = _scan_router_error_literals()
    http_dynamic = {c for c in found if c.startswith("http_") and c[5:].isdigit()}
    assert http_dynamic == set(), (
        f"`http_<n>` literal trovati come domain code: "
        f"{sorted(http_dynamic)}. Questi devono passare via "
        f"`http_status_code()` factory, non come literal nei router."
    )
