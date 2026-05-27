"""
PLENORA-ETL — Catalog dei codici stringa per error envelope

"""

from __future__ import annotations

from app.core.admin_backups.errors import (
    KNOWN_DOMAIN_CODES as ADMIN_BACKUPS_DOMAIN_CODES,
)
from app.core.connections.errors import (
    KNOWN_DOMAIN_CODES as CONNECTIONS_DOMAIN_CODES,
)
from app.core.datasources.errors import (
    KNOWN_DOMAIN_CODES as DATASOURCES_DOMAIN_CODES,
)
from app.core.tickets.errors import (
    KNOWN_DOMAIN_CODES as TICKETS_DOMAIN_CODES,
)


# Generic / framework-level

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


# Domain-specific definitions still owned by the shared envelope/catalog layer.

API_CONNECTION_IN_USE = "api_connection_in_use"
"""409: tentativo di delete di un'api_connection referenziata."""

PROJECT_HAS_DEPENDENCIES = "project_has_dependencies"
"""409: tentativo di delete progetto con discendenti."""

PROJECT_CLEANUP_PARTIAL = "project_cleanup_partial"
"""500: cleanup progetto fallito su step derivati (row preservata)."""

CARICA_LAUNCH_FAILED = "carica_launch_failed"
"""500-state: lancio task carica fallito (orchestrator/service)."""

CARICA_SCHEDULER_FAILED = "carica_scheduler_failed"
"""500-state: lancio carica da scheduler fallito (execution_manager)."""

SCHEDULE_EXECUTION_FATAL = "schedule_execution_fatal"
"""500-state: errore fatale schedule execution (execution_manager)."""


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

API_CONNECTION_NOT_FOUND = "api_connection_not_found"
"""404: API connection non trovata (arricchisci)."""

FILE_SOURCE_NOT_FOUND = "file_source_not_found"
"""404: file source non trovato (files)."""


# Domain-specific — External-Service family
# Promozione mirata della famiglia 502 "upstream/external service
# unreachable" da `http_502` generico a codice domain. Permette al
# FE di distinguere "errore di rete su servizio esterno" (retry
# suggestion, contact admin) da "errore interno BE"
# Status code resta 502. Nessuna esposizione di dettagli upstream
# sensibili (URL, token, response body) — solo il flag semantico

EXTERNAL_SERVICE_UNREACHABLE = "external_service_unreachable"
"""502: servizio esterno non raggiungibile (features/ArcGIS,
arricchisci/OpenAPI). FE branching: retry, fallback offline, o
notifica admin."""


# Domain-specific — Memory/OOM family
# Promozione mirata della famiglia 413 "memory limit exceeded"
# emessa dai preflight guard di OOM-prevention. Permette al FE di
# distinguere "input troppo grande" (suggerire chunking, formato
# alternativo, contact admin) da generic 4xx. Status code 413
# Payload Too Large): semantico HTTP corretto perche' il client
# inviato una richiesta che e' troppo grande per essere
# materializzata in memoria

MEMORY_LIMIT_EXCEEDED = "memory_limit_exceeded"
"""413: input/payload supera il limite di memoria configurato per
lo stage di processing (es. `load_dataframe_from_file`). FE
branching: messaggio "file troppo grande per il caricamento
immediato; contatta l'admin" + suggerimento chunking/streaming."""


SERVICE_NOT_INITIALIZED = "service_not_initialized"
"""500/503: un servizio module-level non e' stato inizializzato
da `main.py` (init_*) prima di servire la richiesta. Failure di
startup, non operational; FE branching: invitare a riprovare e
contattare l'admin se persiste."""


ACCESS_DENIED = "access_denied"
"""403: l'utente non ha i permessi per accedere alla risorsa
richiesta. Coperti: group-ACL (ownership/share/admin-cross-group),
anti-enumeration uniform-403 (reset password / delete user fuori
gruppo o inesistenti per admin), path-traversal denial generico.
FE branching: redirect a list view, toast generico "non
autorizzato"; NON usare per autenticazione mancante (401)."""


# Domain-specific — Auth family
# Promozione dei call site auth-domain (auth.py + ownership.py) da
# literal HTTPException a raise_typed_http preservando status code
# messaggio italiano user-facing e headers (`WWW-Authenticate`
# `Retry-After`). Nessun cambiamento comportamentale, nessuna nuova
# disclosure
# Disclosure policy: vedi ADR-0022 (auth disclosure policy
# uniform-401, admin reset/delete uniform-403, path-traversal

AUTH_TOKEN_MISSING = "auth_token_missing"
"""401 + WWW-Authenticate: Bearer. Token Bearer assente nella
request. Status/message preservati."""

AUTH_TOKEN_INVALID = "auth_token_invalid"
"""401 + WWW-Authenticate: Bearer. Token presente ma decoda fallita
(invalid/expired/firma errata). NON distinguibile expired vs invalid:
disclosure unica per non leakare TTL osservabile."""

AUTH_TOKEN_REVOKED = "auth_token_revoked"
"""401 + WWW-Authenticate: Bearer. Token decoda OK ma jti in
blacklist (logout esplicito). Distinto da `auth_token_invalid` per
UX post-logout (LEAKY_BY_DESIGN: l'utente sa che la revoca ha
funzionato)."""

AUTH_USER_NOT_FOUND_OR_DISABLED = "auth_user_not_found_or_disabled"
"""401 (no WWW-Authenticate). Token valido, ma `db.get_user(sub)`
ritorna None o `is_active=False`. Codice UNICO per i due rami:
anti-enumeration non rivela se l'utente e' stato disabilitato vs
eliminato. NON splittare."""

AUTH_INVALID_CREDENTIALS = "auth_invalid_credentials"
"""401. Username/password sbagliati al login. Constant-time bcrypt
verify applicato anche su user inesistente (auth.py:395
_DUMMY_PASSWORD_HASH). Codice UNICO per "user inesistente" e
"password errata": anti-enumeration."""

AUTH_MFA_INVALID = "auth_mfa_invalid"
"""401. Codice TOTP fornito ma errato al login. Conta su rate-limit
IP (auth.py:417)."""

AUTH_ACCOUNT_DISABLED = "auth_account_disabled"
"""403. Utente esiste, password ok, ma `is_active=False`. Disclosure
post-password-ok intenzionale (UX-required: amministrazione utenti).
LEAKY_BY_DESIGN."""

AUTH_MFA_REQUIRED = "auth_mfa_required"
"""403. Login post-password-ok ma `mfa_code` mancante. Disclosure
intenzionale (LEAKY_BY_DESIGN): ADR-0018 trade-off MFA-aware client
UX. Message preservato come `mfa_required` literal italiano."""

AUTH_ROLE_INSUFFICIENT_FOR_WRITE = "auth_role_insufficient_for_write"
"""403. Viewer/esecutore tentano operazione di scrittura/modifica.
LEAKY_BY_DESIGN (UI-friendly)."""

AUTH_ROLE_KIOSK_ONLY = "auth_role_kiosk_only"
"""403. Esecutore tenta endpoint diverso da `eseguibili list/run`.
LEAKY_BY_DESIGN (UI-friendly)."""

AUTH_ROLE_ADMIN_REQUIRED = "auth_role_admin_required"
"""403. Endpoint richiede ruolo admin o superadmin.
LEAKY_BY_DESIGN (UI-friendly)."""

AUTH_ROLE_SUPERADMIN_REQUIRED = "auth_role_superadmin_required"
"""403. Endpoint richiede ruolo superadmin. LEAKY_BY_DESIGN."""

AUTH_ROLE_CREATE_PRIVILEGE_REQUIRED = "auth_role_create_privilege_required"
"""403. Admin tenta di creare un altro admin/superadmin (privilege
escalation block: solo superadmin). LEAKY_BY_DESIGN."""

AUTH_FIELD_ROLE_RESTRICTED = "auth_field_role_restricted"
"""403. Tentativo di modifica di un campo riservato a ruoli
superiori (es. `group_id` modificabile solo da superadmin).
LEAKY_BY_DESIGN."""

AUTH_SELF_MODIFY_DENIED = "auth_self_modify_denied"
"""403. Self-demotion guard: un utente non puo' modificare il
proprio ruolo. UNIFORM (no info leak)."""

AUTH_SELF_DISABLE_DENIED = "auth_self_disable_denied"
"""403. Self-disable guard: un utente non puo' disabilitare il
proprio account. UNIFORM."""

AUTH_HIERARCHICAL_OWNER_REQUIRED = "auth_hierarchical_owner_required"
"""403. Risorsa creata da superadmin: solo superadmin puo'
modificarla. LEAKY_BY_DESIGN (UI-friendly: spiega *perche'* l'admin
di gruppo non puo' agire)."""

AUTH_OWNER_ONLY = "auth_owner_only"
"""403. La risorsa richiede l'owner originale (created_by) oppure
un admin/superadmin. LEAKY_BY_DESIGN (UI-friendly)."""

USER_NOT_FOUND = "user_not_found"
"""404. Utente target non trovato. NB: per admin non-superadmin, le
operazioni `reset-password`/`delete` su target fuori-gruppo o
inesistente ritornano `access_denied` (403) per anti-enumeration.
Solo superadmin vede 404 distinto."""

GROUP_NOT_FOUND = "group_not_found"
"""404. Gruppo target non trovato. Endpoint admin-only."""

USERNAME_ALREADY_EXISTS = "username_already_exists"
"""409. Username gia' presente. Disclosure intenzionale (admin DEVE
saperlo per gestione utenti). LEAKY_BY_DESIGN."""

GROUP_ID_ALREADY_EXISTS = "group_id_already_exists"
"""409. group_id gia' presente. LEAKY_BY_DESIGN."""

GROUP_DEFAULT_NOT_DELETABLE = "group_default_not_deletable"
"""400. Tentativo di eliminare il gruppo `default`. UNIFORM."""

AUTH_MFA_CODE_INVALID = "auth_mfa_code_invalid"
"""400. Codice TOTP non valido durante enroll/disable MFA. Distinto
da `auth_mfa_invalid` (401, login)."""

AUTH_SELF_DELETE_DENIED = "auth_self_delete_denied"
"""400. Un utente non puo' eliminare il proprio account. UNIFORM."""

AUTH_RATE_LIMIT_EXCEEDED = "auth_rate_limit_exceeded"
"""429 + Retry-After. Lockout login per IP
(LOGIN_LOCKOUT_SECONDS). UNIFORM."""

AUTH_REVOCATION_UNAVAILABLE = "auth_revocation_unavailable"
"""503. Redis blacklist non disponibile durante logout. Fail-loud
(ADR-0009): il client deve sapere se la revoca e' effettiva."""


# Catalog set — drift detection
# `KNOWN_DOMAIN_CODES` espone l'intero catalog domain-specific
# codici NON globali) come `frozenset[str]`. Usato dal test
# contract `test_error_codes_catalog.py` per il drift detection
# source-bound: ogni `"error":\s*"<x>"` literal nei file
# `app/core/**/*.py` deve essere in questo set o tra i codici
# globali (`internal_error`, `canvas_corrupted`
# `validation_error`, `http_<status>`
# Ricostruito automaticamente dalle costanti dichiarate sopra
# per evitare drift fra costante e set. Frozen per immutabilita'
# non modificabile a runtime
KNOWN_DOMAIN_CODES: frozenset[str] = frozenset({
    API_CONNECTION_IN_USE,
    PROJECT_HAS_DEPENDENCIES,
    PROJECT_CLEANUP_PARTIAL,
    CARICA_LAUNCH_FAILED,
    CARICA_SCHEDULER_FAILED,
    SCHEDULE_EXECUTION_FATAL,
    PROJECT_NOT_FOUND,
    TASK_NOT_FOUND,
    SCHEDULE_NOT_FOUND,
    RUN_NOT_FOUND,
    BUNDLE_NOT_FOUND,
    API_CONNECTION_NOT_FOUND,
    FILE_SOURCE_NOT_FOUND,
    EXTERNAL_SERVICE_UNREACHABLE,
    # Memory/OOM family
    MEMORY_LIMIT_EXCEEDED,
    SERVICE_NOT_INITIALIZED,
    ACCESS_DENIED,
    AUTH_TOKEN_MISSING,
    AUTH_TOKEN_INVALID,
    AUTH_TOKEN_REVOKED,
    AUTH_USER_NOT_FOUND_OR_DISABLED,
    AUTH_INVALID_CREDENTIALS,
    AUTH_MFA_INVALID,
    # 403 — autorizzazione
    AUTH_ACCOUNT_DISABLED,
    AUTH_MFA_REQUIRED,
    AUTH_ROLE_INSUFFICIENT_FOR_WRITE,
    AUTH_ROLE_KIOSK_ONLY,
    AUTH_ROLE_ADMIN_REQUIRED,
    AUTH_ROLE_SUPERADMIN_REQUIRED,
    AUTH_ROLE_CREATE_PRIVILEGE_REQUIRED,
    AUTH_FIELD_ROLE_RESTRICTED,
    AUTH_SELF_MODIFY_DENIED,
    AUTH_SELF_DISABLE_DENIED,
    AUTH_HIERARCHICAL_OWNER_REQUIRED,
    AUTH_OWNER_ONLY,
    # 404 — after-auth resource not found
    USER_NOT_FOUND,
    GROUP_NOT_FOUND,
    # 409 — conflict
    USERNAME_ALREADY_EXISTS,
    GROUP_ID_ALREADY_EXISTS,
    # 400 — validation auth-related
    GROUP_DEFAULT_NOT_DELETABLE,
    AUTH_MFA_CODE_INVALID,
    AUTH_SELF_DELETE_DENIED,
    # 429 — rate-limit
    AUTH_RATE_LIMIT_EXCEEDED,
    # 503 — infra fail-loud
    AUTH_REVOCATION_UNAVAILABLE,
}) | (
    ADMIN_BACKUPS_DOMAIN_CODES
    | CONNECTIONS_DOMAIN_CODES
    | DATASOURCES_DOMAIN_CODES
    | TICKETS_DOMAIN_CODES
)

# Codici globali (envelope-level): NON domain-specific
# Mantenuto separato perche' il drift detection deve permettere
# entrambi (`error` puo' essere globale o domain
KNOWN_GLOBAL_CODES: frozenset[str] = frozenset({
    INTERNAL_ERROR,
    CANVAS_CORRUPTED,
    VALIDATION_ERROR,
})


# Pattern accettati come `http_<status>` fallback intenzionale
# Questi pattern di `raise HTTPException(<n>, "<msg>")` NON sono
# residui da promuovere a codici domain. Sono stringhe locali
# stabili gia' coperte semanticamente da
# `internal_error` (500 catch-all globale
# `http_<status>` per i casi senza valore di branching FE
# Documentati esplicitamente per evitare che il drift detection
# li riconti come residui
# Riferimento: classification audit
# sezione "B. ACCEPT_HTTP_STATUS_FALLBACK"
ACCEPTED_HTTP_FALLBACK_PATTERNS: frozenset[str] = frozenset({
    # "internal_error_generic" promoted to raise_typed_http(INTERNAL_ERROR
    # in Zero residuals remain
    # `value_error_str_e` — translation di eccezioni dynamic-validation
    # in `HTTPException(detail=str(e))` o
    # `detail=sanitize_http_detail(str(e))` (eseguibili
    # AUDIT (closure
    # ~40 call site in `app/core/<domain>/router*.py` (eseguibili
    # connections, datasources, tickets, features, projects, carica
    # arricchisci, files, estrai). Pattern unico: catturare un
    # `ValueError`/sub-classe (`MimeMismatchError`
    # `SourceLoadingError`, `ProjectLineageCycleError`, builtin
    # `PermissionError`, `FileNotFoundError`, `JSONDecodeError`) e
    # rilanciare 400/422/404/403 con il messaggio originale dinamico
    # Contract sentinel: dynamic validation messages intentionally stay
    # generic; the catalog test locks callsite count so new uses require
    # explicit review.
    "value_error_str_e",
})
