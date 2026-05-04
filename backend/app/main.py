"""
Entry point dell'applicazione FastAPI PLENORA-ETL.

Architettura (post-rework)
==========================
main.py è ora snello: configura l'app, il CORS middleware, inizializza
i servizi shared e registra i router dei moduli autocontenuti.

Ogni modulo in app/core/<modulo>/ contiene:
- models.py:   modelli Pydantic specifici del dominio
- router.py:   endpoint FastAPI
- executor.py: logica di esecuzione
- service.py:  logica di business (dove applicabile)

Moduli registrati:
- connections  → /api/connections
- projects     → /api/projects (CRUD + canvas + schema)
- files        → /api/files
- estrai       → /api/functions, /api/projects/{id}/generate-sql
- manipola     → /api/transforms
- arricchisci  → /api/enrichment, /api/api-connections, /api/templates
- carica       → /api/carica
- features     → /api/features (ArcGIS Feature Service: connessioni, browse, query, write)
- scheduler    → /api/schedules
- orchestrator → /api/projects/{id}/execute|preview|download, /api/tasks

Porte: backend su 8000, frontend React su 5173
"""

import os
from typing import Tuple
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.shared.cloud_backends import validate_storage_config
from app.utils.logger import get_logger
from app.utils.dataframe import save_dataframe

# Verifica prerequisiti di sistema (DATA_DIR scrivibile, ecc.)
# Interrompe l'avvio con messaggio chiaro se la configurazione è incompleta.
settings.validate_startup()

# A1: coerenza STORAGE_POLICY + provider + bucket + soglie. Fail-fast qui
# evita che un task Celery scopra al primo upload oltre soglia che il bucket
# non esiste o che il provider non e' implementato.
validate_storage_config()

# Inizializza database PostgreSQL (pool + tabelle)
from app.db.db_service import init_all_dbs
init_all_dbs()

# Auth JWT
from app.core.auth.auth import router as auth_router, groups_router, get_current_user
from app.core.auth.auth_middleware import JWTMiddleware
from app.core.auth.request_id_middleware import RequestIdMiddleware
from app.db.cleanup_middleware import DBConnectionCleanupMiddleware

# Fix SSL certificate issue caused by PostgreSQL
os.environ.pop('REQUESTS_CA_BUNDLE', None)
os.environ.pop('SSL_CERT_FILE', None)
os.environ.pop('CURL_CA_BUNDLE', None)

logger = get_logger(__name__)

# ==================== APP ====================

# In produzione (ENVIRONMENT=production) disabilita /docs e /openapi.json
# per non esporre lo schema API pubblicamente. In sviluppo restano attivi.
_is_prod = os.getenv("ENVIRONMENT", os.getenv("ENV", "development")).lower() in ("production", "prod")

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    yield
    # Shutdown: chiudi il pool PostgreSQL
    from app.db.pg_connection import close_pool
    close_pool()
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


# Handler per RequestValidationError (Pydantic 422):
# trasforma la shape FastAPI default `{"detail": [{loc, msg, type}, ...]}`
# in envelope dual-shape go-ready preservando la lista validation
# errors. Compat FE: `detail.errors` espone direttamente la lista
# per consumer non migrati al top-level `details.errors`.
#
# Logging: 422 e' un client error. NESSUN `logger.error`/`warning`:
# evitiamo rumore sui dashboard di metric server-side. La FastAPI
# default non logga; manteniamo lo stesso silenzio.
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

    # Costruisci l'envelope canonico dual-shape e poi aggiungi
    # `errors` come campo top-level del mirror `detail` per
    # backward compat con FE non migrato al top-level
    # `response.json().details.errors`.
    # Il helper resta puro e canonico; la patch `detail.errors`
    # vive qui perche' e' un compat-shim 422-specifico.
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
_RSS_STAGES = frozenset({"pipeline_execute", "worker_rss"})

@app.exception_handler(MemoryLimitExceededError)
async def memory_limit_exceeded_handler(
    request: Request, exc: MemoryLimitExceededError,
):
    is_rss = exc.stage in _RSS_STAGES or exc.path_basename == "worker_process"
    status_code = 503 if is_rss else 413
    suggested_action = "retry_later_or_contact_admin" if is_rss else "split_file_or_contact_admin"
    logger.warning(
        "Memory limit exceeded in stage=%s resource=%s observed=%d limit=%d status=%d",
        exc.stage, exc.path_basename, exc.observed_bytes, exc.limit_bytes, status_code,
    )
    limit_mb = round(exc.limit_bytes / (1024 * 1024), 2)
    observed_mb = round(exc.observed_bytes / (1024 * 1024), 2)
    message = (
        f"Il worker ha superato il limite di memoria ({observed_mb} MB / "
        f"{limit_mb} MB). Riprova piu' tardi o contatta l'amministratore."
    ) if is_rss else (
        f"L'input richiesto ({observed_mb} MB) supera il limite di "
        f"memoria configurato ({limit_mb} MB) per lo stage "
        f"'{exc.stage}'. Contatta l'amministratore o riduci la "
        f"dimensione del file."
    )
    return build_error_response(
        status_code=status_code,
        error=error_codes.MEMORY_LIMIT_EXCEEDED,
        message=message,
        details={
            "stage": exc.stage,
            "limit_bytes": exc.limit_bytes,
            "observed_bytes": exc.observed_bytes,
            "limit_mb": limit_mb,
            "observed_mb": observed_mb,
            "suggested_action": suggested_action,
            "resource": exc.path_basename,
        },
        headers=_cors_headers_for(request),
    )


# ==================== INIZIALIZZA SERVIZI SHARED ====================

from app.services.project_service import ProjectService
from app.services.connection_service import ConnectionService
from app.core.estrai.database_inspector import DatabaseInspector
from app.core.estrai.query_generator import QueryGenerator
from app.db.query_executor import QueryExecutor
from app.core.orchestrator.service import OrchestratorService
from app.core.arricchisci.api_connector import api_connection_service
from app.core.features.features_service import FeaturesService

project_service = ProjectService()
connection_service = ConnectionService()
database_inspector = DatabaseInspector()
query_generator = QueryGenerator()
query_executor = QueryExecutor(results_dir=settings.DATA_DIR / "temp", save_function=save_dataframe)
features_service = FeaturesService(save_function=save_dataframe)

orchestrator = OrchestratorService(
    project_service=project_service,
    executor=query_executor,
    generator=query_generator,
    connection_service=connection_service,
    features_service=features_service
)


# ==================== INIT MODULI ====================

from app.core.connections.router import router as connections_router, init_connections
from app.core.projects.router import router as projects_router, init_projects
from app.core.files.router import router as files_router
from app.core.estrai.router import functions_router
from app.core.manipola.router import router as transforms_router
from app.core.arricchisci.router import (
    enrichment_router, api_connections_router, templates_router,
    enrich_router, init_arricchisci
)
from app.core.carica.router import router as carica_router, init_carica_service
from app.core.features.routes import router as features_router
from app.core.scheduler.router import router as schedules_router
from app.core.orchestrator.router import pipeline_router, tasks_router, init_orchestrator
from app.core.logs.router import router as logs_router
from app.core.logs.runs_router import router as runs_router
from app.core.datasources.router import router as datasources_router, init_datasources
from app.core.intermediate.router import router as intermediate_router
from app.core.datasources.service import datasource_service
from app.core.eseguibili.router import router as eseguibili_router
from app.core.imdf.router import router as imdf_router
from app.core.auth.shares_router import router as shares_router
from app.core.admin_backups.router import router as admin_backups_router
from app.core.admin_system_status.router import router as admin_system_status_router
from app.core.tickets.router import router as tickets_router

# Inizializza moduli che necessitano di servizi
init_connections(connection_service, database_inspector)
init_projects(project_service)
init_arricchisci(project_service, api_connection_service)
init_datasources(datasource_service)
init_carica_service(connection_service)
init_orchestrator(orchestrator)
# features: usa lazy init interno, nessuna init_* necessaria


# ==================== REGISTRA ROUTER ====================

# Auth (auth in signature di ogni endpoint)
app.include_router(auth_router, prefix="/api")
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

# ==================== HEALTH CHECK ====================

@app.get("/")
def root():
    """Banner root (non e' health check vero — vedi /health)"""
    return {"status": "ok", "service": "PLENORA-ETL API"}


def _check_postgres() -> Tuple[bool, str]:
    """SELECT 1 sul pool PG. Return (ok, detail)."""
    try:
        from app.db.pg_connection import fetchone
        row = fetchone("SELECT 1 AS v")
        if row and int(row.get("v", 0)) == 1:
            return True, "ok"
        return False, f"SELECT 1 ha restituito: {row!r}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _check_redis() -> Tuple[bool, str]:
    """PING su Redis broker Celery."""
    try:
        import redis
        r = redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=2)
        if r.ping():
            return True, "ok"
        return False, "PING non ha risposto True"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _check_migrations_aligned() -> Tuple[bool, str]:
    """C8-nit: /ready rigoroso include check "migrations allineate".
    Confronta files in migrations/ con righe in schema_migrations.
    503 se ci sono pending (deploy a meta' / file committato ma non
    ancora applicato dal runner)."""
    try:
        from app.db.migrations_runner import list_pending_migrations
        pending = list_pending_migrations()
        if pending:
            return False, f"{len(pending)} pending: {', '.join(pending[:3])}"
        return True, "aligned"
    except Exception as e:
        # Non falliamo /ready per un problema di introspection stessa —
        # un check PG/Redis a monte ha gia' validato la raggiungibilita'.
        return True, f"check skipped: {type(e).__name__}"


def _check_pool_headroom() -> Tuple[bool, str]:
    """Verifica che il pool PG non sia saturo: almeno 1 slot libero."""
    try:
        from app.db.pg_connection import _get_pool, _POOL_MAX
        pool = _get_pool()
        # psycopg2.pool._used e' lista conn in uso, _pool e' lista free.
        # Accesso interno — best-effort diagnostic.
        in_use = len(getattr(pool, "_used", {}) or {})
        free = len(getattr(pool, "_pool", []) or [])
        used_total = in_use
        if used_total >= _POOL_MAX:
            return False, f"pool saturo: {used_total}/{_POOL_MAX} in uso, {free} free"
        return True, f"{used_total}/{_POOL_MAX} in uso, {free} free"
    except Exception as e:
        # Se non riusciamo a ispezionare, non falliamo /ready —
        # un SELECT 1 e' gia' successo sopra.
        return True, f"introspection skipped: {type(e).__name__}"


@app.get("/health")
def health():
    """C8: liveness + dependency check (PG SELECT 1 + Redis PING).

    Ritorna 200 se entrambe le dipendenze critiche rispondono, 503
    altrimenti. Pensato per load balancer / K8s readinessProbe.
    """
    checks = {}
    status_code = 200

    pg_ok, pg_detail = _check_postgres()
    checks["postgres"] = {"ok": pg_ok, "detail": pg_detail}
    if not pg_ok:
        status_code = 503

    redis_ok, redis_detail = _check_redis()
    checks["redis"] = {"ok": redis_ok, "detail": redis_detail}
    if not redis_ok:
        status_code = 503

    return JSONResponse(
        content={
            "status": "ok" if status_code == 200 else "unhealthy",
            "checks": checks,
        },
        status_code=status_code,
    )


@app.get("/ready")
def ready():
    """C8: readiness check rigoroso. Include:
    - /health checks (PG + Redis)
    - headroom sul pool PG (non saturo)

    Pensato per K8s readinessProbe conservativo (rimuove pod dal pool
    di load balancing se qualcosa e' degradato anche non-fatal).
    """
    checks = {}
    status_code = 200

    pg_ok, pg_detail = _check_postgres()
    checks["postgres"] = {"ok": pg_ok, "detail": pg_detail}
    if not pg_ok:
        status_code = 503

    redis_ok, redis_detail = _check_redis()
    checks["redis"] = {"ok": redis_ok, "detail": redis_detail}
    if not redis_ok:
        status_code = 503

    pool_ok, pool_detail = _check_pool_headroom()
    checks["pool_headroom"] = {"ok": pool_ok, "detail": pool_detail}
    if not pool_ok:
        status_code = 503

    mig_ok, mig_detail = _check_migrations_aligned()
    checks["migrations"] = {"ok": mig_ok, "detail": mig_detail}
    if not mig_ok:
        status_code = 503

    return JSONResponse(
        content={
            "status": "ready" if status_code == 200 else "not_ready",
            "checks": checks,
        },
        status_code=status_code,
    )


# ==================== STARTUP ====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
