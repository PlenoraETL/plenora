"""
Entry point dell'applicazione FastAPI PLENORA-ETL.

Architettura (post-rework)
"""

import os
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.utils.logger import get_logger
from app.utils.dataframe import save_dataframe

# Bootstrap esplicito: una sola boundary tipata che esegue, in ordine:
#   1. settings.validate_startup()
#   2. validate_storage_config()
#   3. init_all_dbs()
#   4. _repair_psycopg2_ssl_env()  (pop dei 3 SSL env var dopo init PG)
# Vedi `app/bootstrap.py` per il contratto.
from app.bootstrap import bootstrap as _bootstrap, teardown as _teardown
_bootstrap()

# Auth JWT
# BE-GO-READY-AUTH-ENGINE-019: removed unused `get_current_user` dep-import.
from app.core.auth.auth import router as auth_router
from app.core.auth.users_router import users_router
from app.core.auth.groups_router import groups_router
from app.core.auth.auth_middleware import JWTMiddleware
from app.core.auth.request_id_middleware import RequestIdMiddleware
from app.db.cleanup_middleware import DBConnectionCleanupMiddleware

logger = get_logger(__name__)


# In produzione (ENVIRONMENT=production) disabilita /docs e /openapi.json
# per non esporre lo schema API pubblicamente. In sviluppo restano attivi.
_is_prod = os.getenv("ENVIRONMENT", os.getenv("ENV", "development")).lower() in ("production", "prod")

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    yield
    # Shutdown: delega alla boundary simmetrica del bootstrap.
    _teardown()
    logger.info("PG connection pool chiuso")

app = FastAPI(
    title="PLENORA-ETL API",
    lifespan=lifespan,
    docs_url=None if _is_prod else "/docs",
    redoc_url=None if _is_prod else "/redoc",
    openapi_url=None if _is_prod else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS_LIST,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    # X-Request-ID (C8): accettato in preflight cosi' il frontend puo'
    # mandare il proprio correlation ID (es. da un distributed trace)
    # e noi lo riusiamo nel ContextVar invece di generarne uno nuovo.
    allow_headers=[
        "Authorization", "Content-Type", "Accept", "X-Requested-With",
        "X-Request-ID",
    ],
    # Headers che il browser e' autorizzato a leggere dal JS client.
    # - Content-Disposition: usato da downloadBlob.js per filename
    # - X-Request-ID (C8): consente al frontend di correlare log
    #   client-side con log server-side sullo stesso request_id.
    expose_headers=["Content-Disposition", "X-Request-ID"],
)
app.add_middleware(JWTMiddleware)
# DB cleanup deve essere il middleware più ESTERNO (FastAPI esegue in LIFO),
# così le connessioni thread-local vengono chiuse DOPO che tutto il request
# processing è terminato. Previene leak di file descriptor nei thread riusati
# dal pool uvicorn.
app.add_middleware(DBConnectionCleanupMiddleware)
# C8: RequestIdMiddleware e' il piu' esterno assoluto. Setta ContextVar
# request_id PRIMA che qualunque altro middleware logghi (JWT errors,
# DBConn errors) cosi' ogni riga di log di quella richiesta e'
# correlata. Legge X-Request-ID se il client lo manda, altrimenti
# genera uuid4. Echo della stessa ID nella response.
app.add_middleware(RequestIdMiddleware)


# Global exception handlers — garantiscono shape envelope go-ready
# (BE-GO-READY-ERROR-CONTRACT-002) e header CORS anche su eccezioni
# non gestite.
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.shared import error_codes
from app.shared.error_envelope import (
    build_error_envelope_body,
    build_error_response,
    normalize_http_exception_detail,
)

_CORS_ORIGINS = set(settings.CORS_ORIGINS_LIST)


def _cors_headers_for(request: Request) -> dict:
    """Costruisce gli header CORS per la response di errore se
    l'origin matcha la whitelist. Replica esatta della logica
    pre-pilot.
    """
    origin = request.headers.get("origin", "")
    if origin in _CORS_ORIGINS:
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "false",
        }
    return {}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all: eccezioni non gestite → envelope `internal_error`.

    Body dual-shape (`error/message/details` + `detail` mirror) per
    backward compat con i test che lockano `detail.error`.
    """
    logger.error(
        f"Eccezione non gestita: {request.method} {request.url.path} -> {exc}",
        exc_info=True,
    )
    return build_error_response(
        status_code=500,
        error=error_codes.INTERNAL_ERROR,
        message="Errore interno del server",
        details=None,
        headers=_cors_headers_for(request),
    )


# Handler per HTTPException (sia FastAPI che Starlette): normalizza
# il `detail` legacy nelle 3 chiavi canoniche `error`/`message`/
# `details` e wrap-pa in dual-shape envelope. Lascia intatto il
# comportamento status code esistente. NESSUN router viene migrato:
# l'envelope viene applicato centralmente.
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException,
):
    norm = normalize_http_exception_detail(exc.status_code, exc.detail)
    # Header passati dall'eccezione (es. WWW-Authenticate) preservati.
    base_headers = _cors_headers_for(request)
    if exc.headers:
        base_headers.update(exc.headers)
    return build_error_response(
        status_code=exc.status_code,
        error=norm["error"],
        message=norm["message"],
        details=norm["details"],
        headers=base_headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError,
):
    # Sanifica le entry: tieni solo `loc`, `msg`, `type` (campi
    # gia' presenti nella shape FastAPI default). Scarta `ctx` e
    # `input` per evitare di echo-are valori potenzialmente
    # sensibili (PII, secrets, payload utente) nella response.
    errors_list = [
        {
            "loc": list(err.get("loc", [])),
            "msg": str(err.get("msg", "")),
            "type": str(err.get("type", "")),
        }
        for err in exc.errors()
    ]

    body = build_error_envelope_body(
        error=error_codes.VALIDATION_ERROR,
        message="Dati richiesta non validi",
        details={"errors": errors_list},
    )
    body["detail"]["errors"] = errors_list

    return JSONResponse(
        status_code=422,
        content=body,
        headers=_cors_headers_for(request),
    )


# Handler dedicato per canvas corrotto: il messaggio al client deve
# essere esplicito (non "errore interno") cosi' l'utente capisce che
# serve un ripristino da backup, non un retry. Precede il catch-all
# sopra perche' FastAPI matcha il piu' specifico.
from app.shared.exceptions import CanvasCorruptedError, MemoryLimitExceededError


@app.exception_handler(CanvasCorruptedError)
async def canvas_corrupted_handler(request: Request, exc: CanvasCorruptedError):
    logger.error(
        f"Canvas corrotto servito su {request.method} {request.url.path} "
        f"per progetto {exc.project_id}: {exc.reason}",
        exc_info=exc.original_error,
    )
    return build_error_response(
        status_code=500,
        error=error_codes.CANVAS_CORRUPTED,
        message=(
            f"Canvas del progetto '{exc.project_id}' corrotto "
            f"({exc.reason}). Contattare l'amministratore per il "
            f"ripristino da backup."
        ),
        details={
            "project_id": exc.project_id,
            "reason": exc.reason,
        },
        headers=_cors_headers_for(request),
    )


# Handler per MemoryLimitExceededError:
# Mappa l'eccezione pura sollevata dai guard OOM nel dual-shape
# envelope. Status code dipende dallo stage:
# - file preflight (load_dataframe_from_file): 413 Payload Too Large
#   (il client ha inviato un input troppo grande).
# - worker RSS (pipeline_execute, ecc.): 503 Service Unavailable
#   (il worker e' sovraccaricato, riprovare dopo).
#
# Stable details shape: stage / limit_bytes / observed_bytes /
# limit_mb / observed_mb / suggested_action / resource. Permette
# al FE di mostrare un messaggio uniforme. NESSUN absolute path.
#
# BE-GO-READY-TRANSFORMATION-ENGINE-001-CROSS-JOIN-GUARD: lo stage
# `manipola_cross_join` riusa lo stesso contratto ma carries row
# counts (non bytes) in observed/limit_bytes. Il messaggio e'
# row-count-aware; details aggiunge `limit_rows`/`observed_rows`
# per FE convenience (le 7 chiavi base restano stabili).
# BE-GO-READY-TRANSFORMATION-ENGINE-002-MULTI-JOIN-ROW-ESTIMATE:
# stage `manipola_multi_join` aggiunto al row-count set per il
# guard pre-merge del multi-join sequenziale (executor.py).
# Il messaggio differenzia la remediation multi-join via stage.
_RSS_STAGES = frozenset({"pipeline_execute", "worker_rss"})
_ROW_COUNT_STAGES = frozenset({"manipola_cross_join", "manipola_multi_join"})

@app.exception_handler(MemoryLimitExceededError)
async def memory_limit_exceeded_handler(
    request: Request, exc: MemoryLimitExceededError,
):
    is_rss = exc.stage in _RSS_STAGES or exc.path_basename == "worker_process"
    status_code = 503 if is_rss else 413
    suggested_action = "retry_later_or_contact_admin" if is_rss else "split_file_or_contact_admin"
    is_row_count = exc.stage in _ROW_COUNT_STAGES
    if is_row_count:
        # Row-count stages (cross-join): observed/limit_bytes carry
        # row counts. Override action label to reflect the user
        # remediation (split/filter inputs, not "split file").
        suggested_action = "reduce_inputs_or_raise_cap"
    logger.warning(
        "Memory limit exceeded in stage=%s resource=%s observed=%d limit=%d status=%d",
        exc.stage, exc.path_basename, exc.observed_bytes, exc.limit_bytes, status_code,
    )
    limit_mb = round(exc.limit_bytes / (1024 * 1024), 2)
    observed_mb = round(exc.observed_bytes / (1024 * 1024), 2)
    if is_rss:
        message = (
            f"Il worker ha superato il limite di memoria ({observed_mb} MB / "
            f"{limit_mb} MB). Riprova piu' tardi o contatta l'amministratore."
        )
    elif is_row_count:
        # Stage-specific narrative: i numeri rappresentano row counts
        # (osservato vs limite), MB nei _mb fields non rilevanti per
        # il branch row-count.
        if exc.stage == "manipola_multi_join":
            _verb = "genererebbe"
            _hint = (
                "Riduci la dimensione degli input, rendi le chiavi di join "
                "piu' selettive, o aggiungi filtri "
                "su chiavi non-unique a monte, oppure alza il cap."
            )
        else:
            # cross-join (default row-count narrative).
            _verb = "genererebbe"
            _hint = (
                "Riduci la dimensione degli input (filtri/aggregazioni "
                "a monte) o alza il cap."
            )
        message = (
            f"Lo stage '{exc.stage}' {_verb} {exc.observed_bytes} righe, "
            f"oltre il limite configurato di {exc.limit_bytes} righe. "
            f"{_hint}"
        )
    else:
        message = (
            f"L'input richiesto ({observed_mb} MB) supera il limite di "
            f"memoria configurato ({limit_mb} MB) per lo stage "
            f"'{exc.stage}'. Contatta l'amministratore o riduci la "
            f"dimensione del file."
        )
    details = {
        "stage": exc.stage,
        "limit_bytes": exc.limit_bytes,
        "observed_bytes": exc.observed_bytes,
        "limit_mb": limit_mb,
        "observed_mb": observed_mb,
        "suggested_action": suggested_action,
        "resource": exc.path_basename,
    }
    if is_row_count:
        # Additive keys, the 7 base keys above remain stable.
        details["limit_rows"] = exc.limit_bytes
        details["observed_rows"] = exc.observed_bytes
    return build_error_response(
        status_code=status_code,
        error=error_codes.MEMORY_LIMIT_EXCEEDED,
        message=message,
        details=details,
        headers=_cors_headers_for(request),
    )


from app.runtime import build_runtime_services

_runtime_services = build_runtime_services(
    settings=settings,
    save_function=save_dataframe,
)

project_service = _runtime_services.project_service
connection_service = _runtime_services.connection_service
database_inspector = _runtime_services.database_inspector
query_generator = _runtime_services.query_generator
query_executor = _runtime_services.query_executor
features_service = _runtime_services.features_service
orchestrator = _runtime_services.orchestrator
api_connection_service = _runtime_services.api_connection_service
datasource_service = _runtime_services.datasource_service


from app.core.connections.router import router as connections_router
from app.core.projects.router import router as projects_router
from app.core.files.router import router as files_router
from app.core.estrai.router import functions_router
from app.core.manipola.router import router as transforms_router
from app.core.arricchisci.router import (
    enrichment_router, api_connections_router, templates_router,
    enrich_router,
)
from app.core.carica.router import router as carica_router
from app.core.features.routes import router as features_router
from app.core.scheduler.router import router as schedules_router
from app.core.orchestrator.router import pipeline_router, tasks_router
from app.core.logs.router import router as logs_router
from app.core.logs.runs_router import router as runs_router
from app.core.datasources.router import router as datasources_router
from app.core.intermediate.router import router as intermediate_router
from app.core.eseguibili.router import router as eseguibili_router
from app.core.imdf.router import router as imdf_router
from app.core.auth.shares_router import router as shares_router
from app.core.admin_backups.router import router as admin_backups_router
from app.core.admin_system_status.router import router as admin_system_status_router
from app.core.tickets.router import router as tickets_router

from app.runtime.router_registry import (
    bind_router_services,
    ARRICCHISCI_BINDING,
    CARICA_BINDING,
    CONNECTIONS_BINDING,
    DATASOURCES_BINDING,
    ORCHESTRATOR_BINDING,
    PROJECTS_BINDING,
)
bind_router_services(_runtime_services, CONNECTIONS_BINDING)
bind_router_services(_runtime_services, PROJECTS_BINDING)
bind_router_services(_runtime_services, ARRICCHISCI_BINDING)
bind_router_services(_runtime_services, DATASOURCES_BINDING)
bind_router_services(_runtime_services, CARICA_BINDING)
bind_router_services(_runtime_services, ORCHESTRATOR_BINDING)
# features: usa lazy init interno, nessuna init_* necessaria


# Auth (auth in signature di ogni endpoint)
app.include_router(auth_router, prefix="/api")
app.include_router(users_router, prefix="/api")
app.include_router(groups_router, prefix="/api")
app.include_router(shares_router, prefix="/api")

# Connections (auth dichiarata in signature di ogni endpoint)
app.include_router(connections_router, prefix="/api")

# Projects (CRUD + canvas + schema)
app.include_router(projects_router, prefix="/api")

# Files
app.include_router(files_router, prefix="/api")

# Estrai (functions catalog + translate)
app.include_router(functions_router, prefix="/api")

# Manipola (transforms registry)
app.include_router(transforms_router, prefix="/api")

# Arricchisci (enrichment + api-connections + templates)
app.include_router(enrichment_router, prefix="/api")
app.include_router(api_connections_router, prefix="/api")
app.include_router(templates_router, prefix="/api")
app.include_router(enrich_router, prefix="/api")

# Carica
app.include_router(carica_router, prefix="/api")

# Features (ArcGIS Feature Service: connessioni, browse, query, write)
app.include_router(features_router, prefix="/api")

# Scheduler
app.include_router(schedules_router, prefix="/api")

# Orchestrator (execute + preview + download + tasks)
app.include_router(pipeline_router, prefix="/api")
app.include_router(tasks_router, prefix="/api")

# Logs (consultazione dal frontend)
app.include_router(logs_router, prefix="/api")
app.include_router(runs_router, prefix="/api")

# Datasources
app.include_router(datasources_router, prefix="/api")
app.include_router(intermediate_router, prefix="/api")

# Eseguibili (catene on-demand)
app.include_router(eseguibili_router, prefix="/api")

# IMDF (publish async su ArcGIS)
app.include_router(imdf_router, prefix="/api")

# Admin Backups (superadmin only)
app.include_router(admin_backups_router, prefix="/api")

# Admin System Status dashboard (superadmin only)
app.include_router(admin_system_status_router, prefix="/api")

# Tickets (tech_debt + user_support, RBAC nel router)
app.include_router(tickets_router, prefix="/api")


@app.get("/")
def root():
    """Banner root (non e' health check vero — vedi /health)"""
    return {"status": "ok", "service": "PLENORA-ETL API"}


# BE-GO-READY-BOOTSTRAP-ENGINE-009-HEALTH-EXTRACTION:
# Le 4 funzioni `_check_*` + payload composer sono state estratte in
# `app/observability/health.py`. Le route restano qui per preservare
# il routing wiring FastAPI; delegano a `gather_liveness` /
# `gather_readiness` che restituiscono `HealthPayload` typed.
from app.observability.health import gather_liveness, gather_readiness


@app.get("/health")
def health():
    """C8: liveness + dependency check (PG SELECT 1 + Redis PING).

    Ritorna 200 se entrambe le dipendenze critiche rispondono, 503
    altrimenti. Pensato per load balancer / K8s readinessProbe.
    """
    payload = gather_liveness()
    return JSONResponse(content=payload.body, status_code=payload.status_code)


@app.get("/ready")
def ready():
    """C8: readiness check rigoroso. Include:
    - /health checks (PG + Redis)
    - headroom sul pool PG (non saturo)
    - migrations allineate al codice committato

    Pensato per K8s readinessProbe conservativo (rimuove pod dal pool
    di load balancing se qualcosa e' degradato anche non-fatal).
    """
    payload = gather_readiness()
    return JSONResponse(content=payload.body, status_code=payload.status_code)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
