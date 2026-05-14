"""
Entry point dell'applicazione FastAPI PLENORA-ETL.

Compone bootstrap, middleware, dependency wiring, router e health endpoints.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config_env_loader import load_backend_dotenv

load_backend_dotenv(logging.getLogger("app.config_env_loader"))

from app.config import settings
from app.utils.logger import get_logger
from app.shared import error_codes
from app.shared.error_envelope import (
    build_error_envelope_body,
    build_error_response,
    normalize_http_exception_detail,
)
# Bootstrap esplicito: valida config, storage e DB prima di creare l'app.
# Vedi `app/bootstrap.py` per il contratto completo.
from app.bootstrap import bootstrap as _bootstrap, teardown as _teardown
_bootstrap()

# `from app.core.*` imports MUST come after `_bootstrap()`: the
# dependency-graph lock (`test_main_py_bootstrap_runs_before_router_imports`)
# enforces routers/core load only against an initialised app.
from app.core.dataframes.frame_io import save_dataframe
from app.core.projects.exceptions import CanvasCorruptedError
from app.core.resources.exceptions import MemoryLimitExceededError
from app.middleware_stack import install_middleware_stack

logger = get_logger(__name__)


# Politica esposizione doc endpoints:
#  - /openapi.json: sempre attivo (anche in prod). Lo schema OpenAPI e' la
#    base per validator FE, contract test cross-stack, generazione client.
#    Tenerlo solo in dev produce drift FE↔BE (12 bug della sessione di
#    audit, vedi commit 1d5e1ac). I dati esposti sono la SHAPE degli
#    endpoint, non i loro segreti — equivalente di leggere il sorgente
#    aperto su GitHub.
#  - /docs e /redoc: solo in dev/staging. Sono UI interattive per esplorare
#    l'API, non serve esporle al pubblico produzione (riducono la
#    superficie di reconnaissance senza valore aggiunto per gli utenti
#    reali, che usano il FE).


def _compute_doc_endpoints(env_value: str | None) -> tuple[str | None, str | None, str]:
    """Decide docs_url, redoc_url, openapi_url in base all'env.

    Estratto come funzione pura per facilitare il lock contract test
    (`test_doc_endpoints_policy_contract`): la stessa logica testata
    su 'production', 'prod', 'staging', 'development', None.

    Ritorna (docs_url, redoc_url, openapi_url) con None = endpoint
    disabilitato. openapi_url e' sempre stringa non-None.
    """
    env_normalized = (env_value or "development").strip().lower()
    is_prod = env_normalized in ("production", "prod")
    return (
        None if is_prod else "/docs",
        None if is_prod else "/redoc",
        "/openapi.json",
    )


_docs_url, _redoc_url, _openapi_url = _compute_doc_endpoints(
    os.getenv("ENVIRONMENT", os.getenv("ENV", "development"))
)


@asynccontextmanager
async def lifespan(app):
    yield
    # Shutdown: delega alla boundary simmetrica del bootstrap.
    _teardown()
    logger.info("PG connection pool chiuso")

app = FastAPI(
    title="PLENORA-ETL API",
    lifespan=lifespan,
    docs_url=_docs_url,
    redoc_url=_redoc_url,
    openapi_url=_openapi_url,
)

install_middleware_stack(app, cors_origins=settings.CORS_ORIGINS_LIST)


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


from app.core.api import api_router
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

app.include_router(api_router, prefix="/api")


@app.get("/")
def root():
    """Banner root (non e' health check vero — vedi /health)"""
    return {"status": "ok", "service": "PLENORA-ETL API"}


# Le route health restano nel composition root e delegano a observability.
from app.observability.health import gather_liveness, gather_readiness


@app.get("/health")
def health():
    """Lightweight liveness check.

    Ritorna 200 se il processo FastAPI risponde. Le dipendenze
    critiche sono verificate da /ready per evitare restart su lag
    transitorio di PG/Redis.
    """
    payload = gather_liveness()
    return JSONResponse(content=payload.body, status_code=payload.status_code)


@app.get("/ready")
def ready():
    """Readiness check rigoroso. Include:
    - deep PG SELECT 1 e Redis PING bounded
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
