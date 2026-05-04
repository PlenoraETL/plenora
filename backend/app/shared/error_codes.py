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
# Domain-specific — Not-Found family
# (BE-GO-READY-ERROR-CODES-009-NOT-FOUND-PROMOTION)
# ============================================================
# Promozione mirata di 10 famiglie 404 da `http_404` generico a
# codici domain. FE puo' branchare differente UX (redirect,
# toast, retry, refresh-list) per tipo di risorsa.
# Status code resta sempre 404. Messaggi italiani user-facing
# invariati.

PROJECT_NOT_FOUND = "project_not_found"
"""404: progetto non trovato (projects/orchestrator/scheduler/imdf/arricchisci)."""

TASK_NOT_FOUND = "task_not_found"
"""404: task non trovato (orchestrator)."""

SCHEDULE_NOT_FOUND = "schedule_not_found"
"""404: schedule non trovato (scheduler)."""

RUN_NOT_FOUND = "run_not_found"
"""404: run pipeline non trovata (intermediate/logs/scheduler)."""

BUNDLE_NOT_FOUND = "bundle_not_found"
"""404: bundle IMDF non trovato/scaduto (imdf)."""

CONNECTION_NOT_FOUND = "connection_not_found"
"""404: connessione DB non trovata (carica/connections/features)."""

API_CONNECTION_NOT_FOUND = "api_connection_not_found"
"""404: API connection non trovata (arricchisci)."""

FILE_SOURCE_NOT_FOUND = "file_source_not_found"
"""404: file source non trovato (files)."""

DATASOURCE_NOT_FOUND = "datasource_not_found"
"""404: data source non trovato (datasources)."""

TICKET_NOT_FOUND = "ticket_not_found"
"""404: ticket o allegato ticket non trovato (tickets)."""


# ============================================================
# Domain-specific — External-Service family
# (BE-GO-READY-ERROR-CODES-010-EXTERNAL-SERVICE)
# ============================================================
# Promozione mirata della famiglia 502 "upstream/external service
# unreachable" da `http_502` generico a codice domain. Permette al
# FE di distinguere "errore di rete su servizio esterno" (retry
# suggestion, contact admin) da "errore interno BE".
# Status code resta 502. Nessuna esposizione di dettagli upstream
# sensibili (URL, token, response body) — solo il flag semantico.

EXTERNAL_SERVICE_UNREACHABLE = "external_service_unreachable"
"""502: servizio esterno non raggiungibile (features/ArcGIS,
arricchisci/OpenAPI). FE branching: retry, fallback offline, o
notifica admin."""


# ============================================================
# Domain-specific — Memory/OOM family
# (BE-GO-READY-MEMORY-OOM-001-LOAD-FILE-PREFLIGHT-GUARD)
# ============================================================
# Promozione mirata della famiglia 413 "memory limit exceeded"
# emessa dai preflight guard di OOM-prevention. Permette al FE di
# distinguere "input troppo grande" (suggerire chunking, formato
# alternativo, contact admin) da generic 4xx. Status code 413
# (Payload Too Large): semantico HTTP corretto perche' il client
# inviato una richiesta che e' troppo grande per essere
# materializzata in memoria.

MEMORY_LIMIT_EXCEEDED = "memory_limit_exceeded"
"""413: input/payload supera il limite di memoria configurato per
lo stage di processing (es. `load_dataframe_from_file`). FE
branching: messaggio "file troppo grande per il caricamento
immediato; contatta l'admin" + suggerimento chunking/streaming."""


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
    # Not-Found family (BE-GO-READY-ERROR-CODES-009)
    PROJECT_NOT_FOUND,
    TASK_NOT_FOUND,
    SCHEDULE_NOT_FOUND,
    RUN_NOT_FOUND,
    BUNDLE_NOT_FOUND,
    CONNECTION_NOT_FOUND,
    API_CONNECTION_NOT_FOUND,
    FILE_SOURCE_NOT_FOUND,
    DATASOURCE_NOT_FOUND,
    TICKET_NOT_FOUND,
    # External-Service family (BE-GO-READY-ERROR-CODES-010)
    EXTERNAL_SERVICE_UNREACHABLE,
    # Memory/OOM family (BE-GO-READY-MEMORY-OOM-001)
    MEMORY_LIMIT_EXCEEDED,
})

# Codici globali (envelope-level): NON domain-specific.
# Mantenuto separato perche' il drift detection deve permettere
# entrambi (`error` puo' essere globale o domain).
KNOWN_GLOBAL_CODES: frozenset[str] = frozenset({
    INTERNAL_ERROR,
    CANVAS_CORRUPTED,
    VALIDATION_ERROR,
})


# ============================================================
# Pattern accettati come `http_<status>` fallback intenzionale
# (BE-GO-READY-ERROR-CODES-009-NOT-FOUND-PROMOTION)
# ============================================================
# Questi pattern di `raise HTTPException(<n>, "<msg>")` NON sono
# residui da promuovere a codici domain. Sono stringhe locali
# stabili gia' coperte semanticamente da:
#   - `internal_error` (500 catch-all globale);
#   - `http_<status>` per i casi senza valore di branching FE.
#
# Documentati esplicitamente per evitare che il drift detection
# li riconti come residui.
#
# Riferimento: classification audit
# `BE-GO-READY-ERROR-CODES-008-STRING-DETAIL-CLASSIFICATION`,
# sezione "B. ACCEPT_HTTP_STATUS_FALLBACK".
ACCEPTED_HTTP_FALLBACK_PATTERNS: frozenset[str] = frozenset({
    # "internal_error_generic" promoted to raise_typed_http(INTERNAL_ERROR)
    # in BE-GO-READY-ERROR-CODES-011. Zero residuals remain.
    #
    # raise HTTPException(400|422, str(e))
    # ~20 occorrenze di translation da ValueError/TypeError
    # locali. Il messaggio e' dinamico, lo status code basta
    # per il branching FE; promotion non e' actionable
    # (un codice diverso per ogni ValueError = noise).
    "value_error_str_e",
    # raise HTTPException(500|503, "<X> non inizializzato")
    # 5 occ. (carica/datasources/projects). Failure di startup,
    # mai in prod operational. Codice domain non aggiunge valore.
    "service_init_guard",
    # raise HTTPException(403, "Accesso negato")
    # ~5 occ. generic ACL. Codici granulari saranno valutati nel
    # batch dedicato auth-aligned.
    "generic_access_denied",
})
