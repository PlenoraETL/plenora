"""
Entry point dell'applicazione FastAPI PLENORA-ETL.

Compone bootstrap, middleware, dependency wiring, router e health endpoints.
"""

import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.utils.logger import get_logger
from app.utils.dataframe import save_dataframe
from app.shared import error_codes
from app.shared.error_envelope import (
    build_error_envelope_body,
    build_error_response,
    normalize_http_exception_detail,
)
from app.shared.exceptions import CanvasCorruptedError, MemoryLimitExceededError

# Bootstrap esplicito: valida config, storage e DB prima di creare l'app.
# Vedi `app/bootstrap.py` per il contratto completo.
from app.bootstrap import bootstrap as _bootstrap, teardown as _teardown
_bootstrap()

# Auth JWT
from app.core.auth.auth import router as auth_router
from app.core.auth.users_router import users_router
from app.core.auth.groups_router import groups_router
from app.core.auth.auth_middleware import JWTMiddleware
from app.core.auth.request_id_middleware import RequestIdMiddleware
from app.db.cleanup_middleware import DBConnectionCleanupMiddleware

logger = get_logger(__name__)


# In produzione disabilita /docs e /openapi.json per non esporre lo schema API.
_is_prod = os.getenv("ENVIRONMENT", os.getenv("ENV", "development")).lower() in ("production", "prod")

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
    # Accetta il correlation ID del frontend e lo riusa nei log server-side.
    allow_headers=[
        "Authorization", "Content-Type", "Accept", "X-Requested-With",
        "X-Request-ID",
    ],
    # Espone filename dei download e correlation ID al client.
    expose_headers=["Content-Disposition", "X-Request-ID"],
)
app.add_middleware(JWTMiddleware)
# DB cleanup deve essere il middleware piu' esterno: FastAPI esegue in LIFO,
# quindi le connessioni thread-local vengono chiuse dopo il request processing.
app.add_middleware(DBConnectionCleanupMiddleware)
# RequestIdMiddleware deve essere ultimo in registrazione per essere eseguito
# per primo: setta il request_id prima che JWT/DB cleanup possano loggare.
app.add_middleware(RequestIdMiddleware)


# Global exception handlers: garantiscono envelope uniforme e header CORS
# anche su eccezioni non gestite.
_CORS_ORIGINS = set(settings.CORS_ORIGINS_LIST)


def _cors_headers_for(request: Request) -> dict:
    """Costruisce gli header CORS per la response di errore se l'origin
    matcha la whitelist.
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


# Handler per HTTPException: normalizza `detail` e applica l'envelope centrale.
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException,
):
    norm = normalize_http_exception_detail(exc.status_code, exc.detail)
    # Preserva header passati dall'eccezione, ad esempio WWW-Authenticate.
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
    # Scarta `ctx` e `input` per non echo-are PII, secret o payload utente.
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


# Handler per MemoryLimitExceededError.
# Mappa i guard OOM su 413 per input troppo grandi e 503 per worker saturi.
# Gli stage row-count riusano lo stesso envelope senza esporre path assoluti.
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
        # Gli stage row-count chiedono riduzione input o cap dedicato.
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
        # Qui observed/limit rappresentano righe, non byte.
        if exc.stage == "manipola_multi_join":
            _verb = "genererebbe"
            _hint = (
                "Riduci la dimensione degli input, rendi le chiavi di join "
                "piu' selettive, o aggiungi filtri "
                "su chiavi non-unique a monte, oppure alza il cap."
            )
        else:
            # Cross-join: narrativa row-count standard.
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
        # Chiavi additive: le chiavi base dell'envelope restano stabili.
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
# Features usa lazy init interno.


# Auth.
app.include_router(auth_router, prefix="/api")
app.include_router(users_router, prefix="/api")
app.include_router(groups_router, prefix="/api")
app.include_router(shares_router, prefix="/api")

# Connections.
app.include_router(connections_router, prefix="/api")

# Projects: CRUD, canvas e schema.
app.include_router(projects_router, prefix="/api")

# Files
app.include_router(files_router, prefix="/api")

# Estrai: catalogo funzioni e traduzione query.
app.include_router(functions_router, prefix="/api")

# Manipola: registry trasformazioni.
app.include_router(transforms_router, prefix="/api")

# Arricchisci: enrichment, api-connections e template.
app.include_router(enrichment_router, prefix="/api")
app.include_router(api_connections_router, prefix="/api")
app.include_router(templates_router, prefix="/api")
app.include_router(enrich_router, prefix="/api")

# Carica
app.include_router(carica_router, prefix="/api")

# Features: ArcGIS Feature Service, browse, query e write.
app.include_router(features_router, prefix="/api")

# Scheduler
app.include_router(schedules_router, prefix="/api")

# Orchestrator: execute, preview, download e task.
app.include_router(pipeline_router, prefix="/api")
app.include_router(tasks_router, prefix="/api")

# Logs: consultazione dal frontend.
app.include_router(logs_router, prefix="/api")
app.include_router(runs_router, prefix="/api")

# Datasources
app.include_router(datasources_router, prefix="/api")
app.include_router(intermediate_router, prefix="/api")

# Eseguibili: catene on-demand.
app.include_router(eseguibili_router, prefix="/api")

# IMDF: publish async su ArcGIS.
app.include_router(imdf_router, prefix="/api")

# Admin Backups: superadmin only.
app.include_router(admin_backups_router, prefix="/api")

# Admin System Status dashboard: superadmin only.
app.include_router(admin_system_status_router, prefix="/api")

# Tickets: tech debt e user support, RBAC nel router.
app.include_router(tickets_router, prefix="/api")


@app.get("/")
def root():
    """Banner root (non e' health check vero — vedi /health)"""
    return {"status": "ok", "service": "PLENORA-ETL API"}


# Le route health restano nel composition root e delegano a observability.
from app.observability.health import gather_liveness, gather_readiness


@app.get("/health")
def health():
    """Liveness check con dipendenze critiche PG e Redis.

    Ritorna 200 se entrambe le dipendenze critiche rispondono, 503
    altrimenti. Pensato per load balancer / K8s readinessProbe.
    """
    payload = gather_liveness()
    return JSONResponse(content=payload.body, status_code=payload.status_code)


@app.get("/ready")
def ready():
    """Readiness check rigoroso. Include:
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
