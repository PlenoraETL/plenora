"""
PLENORA-ETL — Catalog dei codici stringa per error envelope
(BE-GO-READY-ERROR-CONTRACT-002).

Costanti per i codici `error` machine-readable usati dall'envelope
go-ready (`build_error_envelope_body`). Centralizzare i codici
previene typo nei router e fornisce un'unica sorgente di verita'
quando il sistema verra' portato in Go (i tag JSON delle struct di
risposta puntano a queste stringhe).

Convenzioni:
- `snake_case`;
- specificita' progressiva (`http_500` generico, `internal_error`
  per il catch-all, codici domain-specific per casi business);
- nessuna i18n: i codici sono stringhe stabili, i testi italiani
  vivono in `message`.

Inventario iniziale: derivato dai codici gia' usati nei test
contract pre-existing (es. `connection_in_use`,
`project_has_dependencies`) + i nuovi introdotti dal pilot
(`internal_error`, `canvas_corrupted`, `http_<status>`).

NOTA: questo modulo NON valida che ogni router usi solo codici
elencati qui. E' un catalog di riferimento + facility per le import
nei test. I router possono usare stringhe libere durante la
transizione; il ridimensionamento a enum/registry typed e' Phase
go-ready dedicata.
"""

from __future__ import annotations


# ============================================================
# Generic / framework-level
# ============================================================

INTERNAL_ERROR = "internal_error"
"""Catch-all per eccezioni Python non gestite (status 500)."""

CANVAS_CORRUPTED = "canvas_corrupted"
"""Canvas DB-side corrotto/non parsabile (status 500)."""

VALIDATION_ERROR = "validation_error"
"""Errore di validazione richiesta (status 422). Mappato su
`RequestValidationError` di FastAPI/Pydantic."""


def http_status_code(status_code: int) -> str:
    """Genera il codice generico `http_<status>` per
    `HTTPException(detail=<str>)` senza payload strutturato.

    Esempi: `http_404`, `http_409`, `http_400`. Usato come
    fallback dall'`HTTPException` handler quando l'eccezione non
    espone un codice domain-specific.
    """
    return f"http_{status_code}"


# ============================================================
# Domain-specific (codici gia' lockati nei test esistenti)
# ============================================================

CONNECTION_IN_USE = "connection_in_use"
"""409: tentativo di delete di una connection referenziata."""

API_CONNECTION_IN_USE = "api_connection_in_use"
"""409: tentativo di delete di un'api_connection referenziata."""

PROJECT_HAS_DEPENDENCIES = "project_has_dependencies"
"""409: tentativo di delete progetto con discendenti."""

PROJECT_CLEANUP_PARTIAL = "project_cleanup_partial"
"""500: cleanup progetto fallito su step derivati (row preservata)."""

DATASOURCE_IN_USE = "datasource_in_use"
"""409: tentativo di delete di un datasource referenziato."""


# ============================================================
# Domain-specific (BE-GO-READY-ERROR-CODES-006-CATALOG)
# Estensione del catalog per coprire i 6 codici emessi nei
# router che oggi sono literal-only.
# ============================================================

OBJECT_STORAGE_DISABLED = "object_storage_disabled"
"""503: object storage non configurato/disabilitato (admin_backups)."""

AWS_CRT_MISSING = "aws_crt_missing"
"""503: dipendenza `aws-crt` non installata sul server (admin_backups)."""

AWS_CREDENTIALS_EXPIRED = "aws_credentials_expired"
"""503: credenziali AWS scadute o non valide (admin_backups)."""

CARICA_LAUNCH_FAILED = "carica_launch_failed"
"""500-state: lancio task carica fallito (orchestrator/service)."""

CARICA_SCHEDULER_FAILED = "carica_scheduler_failed"
"""500-state: lancio carica da scheduler fallito (execution_manager)."""

SCHEDULE_EXECUTION_FATAL = "schedule_execution_fatal"
"""500-state: errore fatale schedule execution (execution_manager)."""


# ============================================================
# Catalog set — drift detection
# ============================================================
# `KNOWN_DOMAIN_CODES` espone l'intero catalog domain-specific
# (codici NON globali) come `frozenset[str]`. Usato dal test
# contract `test_error_codes_catalog.py` per il drift detection
# source-bound: ogni `"error":\s*"<x>"` literal nei file
# `app/core/**/*.py` deve essere in questo set o tra i codici
# globali (`internal_error`, `canvas_corrupted`,
# `validation_error`, `http_<status>`).
#
# Ricostruito automaticamente dalle costanti dichiarate sopra
# per evitare drift fra costante e set. Frozen per immutabilita'
# (non modificabile a runtime).
KNOWN_DOMAIN_CODES: frozenset[str] = frozenset({
    CONNECTION_IN_USE,
    API_CONNECTION_IN_USE,
    PROJECT_HAS_DEPENDENCIES,
    PROJECT_CLEANUP_PARTIAL,
    DATASOURCE_IN_USE,
    OBJECT_STORAGE_DISABLED,
    AWS_CRT_MISSING,
    AWS_CREDENTIALS_EXPIRED,
    CARICA_LAUNCH_FAILED,
    CARICA_SCHEDULER_FAILED,
    SCHEDULE_EXECUTION_FATAL,
})

# Codici globali (envelope-level): NON domain-specific.
# Mantenuto separato perche' il drift detection deve permettere
# entrambi (`error` puo' essere globale o domain).
KNOWN_GLOBAL_CODES: frozenset[str] = frozenset({
    INTERNAL_ERROR,
    CANVAS_CORRUPTED,
    VALIDATION_ERROR,
})
