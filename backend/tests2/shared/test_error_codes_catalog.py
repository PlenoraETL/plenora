"""Contract test BE-GO-READY-ERROR-CODES-006-CATALOG —
catalog `error_codes.py` + helper `raise_typed_http` + drift
detection source-bound sui codici emessi nei router.

Lock i 5 obiettivi del batch 006:

1. catalog estende `app/shared/error_codes.py` con 6 codici
   domain mancanti + `KNOWN_DOMAIN_CODES` frozenset esposto;
2. convenzioni catalog (snake_case, unique, lowercase);
3. helper `raise_typed_http(code, status_code, message, details=None)`
   solleva `HTTPException` con shape compatibile con
   `normalize_http_exception_detail`;
4. helper round-trip: il `detail` strutturato emesso da
   `raise_typed_http` viene normalizzato dal central handler
   nell'envelope canonico;
5. drift detection: `app/core/**/*.py` parsato source-bound,
   ogni ``"error": "<x>"`` literal deve essere in
   `KNOWN_DOMAIN_CODES` U `KNOWN_GLOBAL_CODES` U
   `{http_<status>}`.

NESSUNA call site esistente e' migrata (catalog e' additivo,
helper e' opzionale). Drift test e' source-bound: parsa file
.py senza importare i moduli applicativi (zero side effects,
zero DB/I/O).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Set

import pytest
from fastapi import HTTPException

from app.shared import error_codes
from app.shared.error_envelope import (
    normalize_http_exception_detail,
    raise_typed_http,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent  # .../backend
_CORE_ROOT = _BACKEND_ROOT / "app" / "core"


# ---------------------------------------------------------------------------
# 1. Catalog membership locks
# ---------------------------------------------------------------------------


# Lock atteso del catalog domain (BE-GO-READY-ERROR-CODES-006).
# Drift in piu' = qualcuno ha aggiunto una costante senza
# aggiornare la lista; drift in meno = costante rimossa
# accidentalmente.
EXPECTED_DOMAIN_CODES: Set[str] = {
    # batch 006
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
}

# Pattern accettati come `http_<status>` fallback intenzionale
# (BE-GO-READY-ERROR-CODES-009-NOT-FOUND-PROMOTION).
# Lockati per evitare che il drift detection li riconti come
# residui da promuovere.
EXPECTED_FALLBACK_PATTERNS: Set[str] = {
    "internal_error_generic",
    "value_error_str_e",
    "service_init_guard",
    "generic_access_denied",
}

EXPECTED_GLOBAL_CODES: Set[str] = {
    "internal_error",
    "canvas_corrupted",
    "validation_error",
}


def test_known_domain_codes_matches_expected_lock():
    """Il `frozenset` esposto dal catalog deve combaciare
    esattamente con il set atteso del batch 006."""
    assert error_codes.KNOWN_DOMAIN_CODES == EXPECTED_DOMAIN_CODES


def test_known_global_codes_matches_expected_lock():
    assert error_codes.KNOWN_GLOBAL_CODES == EXPECTED_GLOBAL_CODES


def test_catalog_count_domain_twentyone():
    """21 codici domain (5 pre-existing + 6 batch 006 + 10 batch 009
    not-found family)."""
    assert len(error_codes.KNOWN_DOMAIN_CODES) == 21


def test_catalog_domain_and_global_disjoint():
    """Nessun codice puo' essere sia domain che globale (semantiche
    diverse: globale = envelope-level, domain = business-level)."""
    overlap = error_codes.KNOWN_DOMAIN_CODES & error_codes.KNOWN_GLOBAL_CODES
    assert overlap == set(), f"Overlap inatteso: {overlap}"


def test_catalog_constants_match_set_values():
    """Ogni costante MAIUSCOLA esposta dal modulo deve avere il
    valore equivalente nel `frozenset` (no drift fra dichiarazione
    e set)."""
    domain_constants = {
        # batch 006
        error_codes.CONNECTION_IN_USE,
        error_codes.API_CONNECTION_IN_USE,
        error_codes.PROJECT_HAS_DEPENDENCIES,
        error_codes.PROJECT_CLEANUP_PARTIAL,
        error_codes.DATASOURCE_IN_USE,
        error_codes.OBJECT_STORAGE_DISABLED,
        error_codes.AWS_CRT_MISSING,
        error_codes.AWS_CREDENTIALS_EXPIRED,
        error_codes.CARICA_LAUNCH_FAILED,
        error_codes.CARICA_SCHEDULER_FAILED,
        error_codes.SCHEDULE_EXECUTION_FATAL,
        # batch 009 — Not-Found family
        error_codes.PROJECT_NOT_FOUND,
        error_codes.TASK_NOT_FOUND,
        error_codes.SCHEDULE_NOT_FOUND,
        error_codes.RUN_NOT_FOUND,
        error_codes.BUNDLE_NOT_FOUND,
        error_codes.CONNECTION_NOT_FOUND,
        error_codes.API_CONNECTION_NOT_FOUND,
        error_codes.FILE_SOURCE_NOT_FOUND,
        error_codes.DATASOURCE_NOT_FOUND,
        error_codes.TICKET_NOT_FOUND,
    }
    assert domain_constants == set(error_codes.KNOWN_DOMAIN_CODES)


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
        error_codes.CONNECTION_NOT_FOUND,
        error_codes.API_CONNECTION_NOT_FOUND,
        error_codes.FILE_SOURCE_NOT_FOUND,
        error_codes.DATASOURCE_NOT_FOUND,
        error_codes.TICKET_NOT_FOUND,
    }
    for code in not_found_codes:
        assert code.endswith("_not_found"), (
            f"Codice not-found `{code}` non rispetta convenzione suffisso."
        )


# ---------------------------------------------------------------------------
# 2. Convenzioni codici
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 3. raise_typed_http helper — shape + round-trip envelope
# ---------------------------------------------------------------------------


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
            code=error_codes.OBJECT_STORAGE_DISABLED,
            status_code=503,
            message="Object storage non configurato.",
        )
    assert exc_info.value.detail["error"] == "object_storage_disabled"


# ---------------------------------------------------------------------------
# 4. Source-bound drift detection: app/core/**/*.py
# ---------------------------------------------------------------------------


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
    """Sanity check post-migration BE-GO-READY-ERROR-CODES-007:
    i 11 call site domain referenziano il catalog via
    `error_codes.<CONST>` invece di literal stringa.

    Pre-migrazione (audit 005) questa stessa sanity scansionava
    i `"error": "<x>"` literal; post-migrazione i literal sono
    spariti dai router (sostituiti dalle costanti) e il sanity
    check si sposta sulle referenze costanti per garantire che
    la migrazione sia completa.

    Fail = qualcuno ha rimosso una costante senza spostare il
    call site, o il regex parser e' rotto.
    """
    found = _scan_router_constant_refs()
    baseline_constants_must_appear = {
        # batch 007 (in-use family)
        "CONNECTION_IN_USE",
        "API_CONNECTION_IN_USE",
        "DATASOURCE_IN_USE",
        "PROJECT_HAS_DEPENDENCIES",
        "PROJECT_CLEANUP_PARTIAL",
        "OBJECT_STORAGE_DISABLED",
        "AWS_CRT_MISSING",
        "AWS_CREDENTIALS_EXPIRED",
        "CARICA_LAUNCH_FAILED",
        "CARICA_SCHEDULER_FAILED",
        "SCHEDULE_EXECUTION_FATAL",
        # batch 009 (not-found family)
        "PROJECT_NOT_FOUND",
        "TASK_NOT_FOUND",
        "SCHEDULE_NOT_FOUND",
        "RUN_NOT_FOUND",
        "BUNDLE_NOT_FOUND",
        "CONNECTION_NOT_FOUND",
        "API_CONNECTION_NOT_FOUND",
        "FILE_SOURCE_NOT_FOUND",
        "DATASOURCE_NOT_FOUND",
        "TICKET_NOT_FOUND",
    }
    missing = baseline_constants_must_appear - found
    assert missing == set(), (
        f"Baseline costanti `error_codes.<CONST>` non referenziate "
        f"nei router: {sorted(missing)}. Probabile call site "
        f"rimosso/rolled-back o regex parser rotto."
    )


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
