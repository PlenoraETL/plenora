"""
FastAPI application factory for PLENORA-ETL.

This module owns the explicit boot path: bootstrap first, then middleware,
exception handlers, runtime services, routers, and health routes.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.shared import error_codes
from app.shared.error_envelope import (
    build_error_envelope_body,
    build_error_response,
    normalize_http_exception_detail,
)
from app.utils.logger import get_logger


logger = get_logger(__name__)


@dataclass(frozen=True)
class AppFactoryDeps:
    """Injectable boundaries for tests and future app construction variants."""

    bootstrap: Callable[[], None]
    teardown: Callable[[], None]
    build_runtime_services: Callable[..., Any]
    save_function: Callable[..., Any] | None = None


def _default_deps() -> AppFactoryDeps:
    from app.bootstrap import bootstrap, teardown
    from app.runtime import build_runtime_services

    return AppFactoryDeps(
        bootstrap=bootstrap,
        teardown=teardown,
        build_runtime_services=build_runtime_services,
    )


def _default_settings() -> Any:
    from app.config import settings

    return settings


def _compute_doc_endpoints(env_value: str | None) -> tuple[str | None, str | None, str]:
    """Decide docs_url, redoc_url, openapi_url in base all'env."""
    env_normalized = (env_value or "development").strip().lower()
    is_prod = env_normalized in ("production", "prod")
    return (
        None if is_prod else "/docs",
        None if is_prod else "/redoc",
        "/openapi.json",
    )


def _settings_env_name(settings: Any) -> str:
    getter = getattr(settings, "get", None)
    if callable(getter):
        return str(getter("ENVIRONMENT", getter("ENV", "development")) or "development")
    return str(
        getattr(settings, "ENVIRONMENT", getattr(settings, "ENV", "development"))
        or "development"
    )


def create_app(
    settings: Any | None = None,
    deps: AppFactoryDeps | None = None,
) -> FastAPI:
    """Build and wire the FastAPI application.

    The boot side effects are intentionally inside this factory path so
    importing `app.app_factory` stays side-effect-light, while `app.main:app`
    still boots the production ASGI app.
    """
    resolved_deps = deps or _default_deps()

    # Bootstrap must complete before any `app.core.*` module graph is loaded.
    resolved_deps.bootstrap()

    resolved_settings = settings if settings is not None else _default_settings()

    # These imports are intentionally post-bootstrap. Several modules transitively
    # touch DB/auth/runtime state and must see an initialized process.
    from app.core.dataframes.frame_io import save_dataframe
    from app.core.projects.exceptions import CanvasCorruptedError
    from app.core.resources.exceptions import MemoryLimitExceededError
    from app.middleware_stack import install_middleware_stack

    docs_url, redoc_url, openapi_url = _compute_doc_endpoints(
        _settings_env_name(resolved_settings)
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        resolved_deps.teardown()
        logger.info("PG connection pool chiuso")

    app = FastAPI(
        title="PLENORA-ETL API",
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
    )
    app.state.settings = resolved_settings

    cors_origins = list(getattr(resolved_settings, "CORS_ORIGINS_LIST", []))
    install_middleware_stack(app, cors_origins=cors_origins)
    cors_origin_set = set(cors_origins)

    def _cors_headers_for(request: Request) -> dict:
        origin = request.headers.get("origin", "")
        if origin in cors_origin_set:
            return {
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Credentials": "false",
            }
        return {}

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
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

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException,
    ):
        norm = normalize_http_exception_detail(exc.status_code, exc.detail)
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

    _RSS_STAGES = frozenset({"pipeline_execute", "worker_rss"})
    _ROW_COUNT_STAGES = frozenset({"manipola_cross_join", "manipola_multi_join"})

    @app.exception_handler(MemoryLimitExceededError)
    async def memory_limit_exceeded_handler(
        request: Request, exc: MemoryLimitExceededError,
    ):
        is_rss = exc.stage in _RSS_STAGES or exc.path_basename == "worker_process"
        status_code = 503 if is_rss else 413
        suggested_action = (
            "retry_later_or_contact_admin"
            if is_rss
            else "split_file_or_contact_admin"
        )
        is_row_count = exc.stage in _ROW_COUNT_STAGES
        if is_row_count:
            suggested_action = "reduce_inputs_or_raise_cap"
        logger.warning(
            "Memory limit exceeded in stage=%s resource=%s observed=%d limit=%d status=%d",
            exc.stage,
            exc.path_basename,
            exc.observed_bytes,
            exc.limit_bytes,
            status_code,
        )
        limit_mb = round(exc.limit_bytes / (1024 * 1024), 2)
        observed_mb = round(exc.observed_bytes / (1024 * 1024), 2)
        if is_rss:
            message = (
                f"Il worker ha superato il limite di memoria ({observed_mb} MB / "
                f"{limit_mb} MB). Riprova piu' tardi o contatta l'amministratore."
            )
        elif is_row_count:
            if exc.stage == "manipola_multi_join":
                _verb = "genererebbe"
                _hint = (
                    "Riduci la dimensione degli input, rendi le chiavi di join "
                    "piu' selettive, o aggiungi filtri "
                    "su chiavi non-unique a monte, oppure alza il cap."
                )
            else:
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
            details["limit_rows"] = exc.limit_bytes
            details["observed_rows"] = exc.observed_bytes
        return build_error_response(
            status_code=status_code,
            error=error_codes.MEMORY_LIMIT_EXCEEDED,
            message=message,
            details=details,
            headers=_cors_headers_for(request),
        )

    save_function = getattr(resolved_deps, "save_function", None) or save_dataframe
    runtime_services = resolved_deps.build_runtime_services(
        settings=resolved_settings,
        save_function=save_function,
    )
    app.state.runtime_services = runtime_services

    from app.core.api import api_router
    from app.runtime.router_registry import (
        ARRICCHISCI_BINDING,
        CARICA_BINDING,
        CONNECTIONS_BINDING,
        DATASOURCES_BINDING,
        ORCHESTRATOR_BINDING,
        PROJECTS_BINDING,
        bind_router_services,
    )

    bind_router_services(runtime_services, CONNECTIONS_BINDING)
    bind_router_services(runtime_services, PROJECTS_BINDING)
    bind_router_services(runtime_services, ARRICCHISCI_BINDING)
    bind_router_services(runtime_services, DATASOURCES_BINDING)
    bind_router_services(runtime_services, CARICA_BINDING)
    bind_router_services(runtime_services, ORCHESTRATOR_BINDING)

    app.include_router(api_router, prefix="/api")

    @app.get("/")
    def root():
        """Banner root (non e' health check vero - vedi /health)."""
        return {"status": "ok", "service": "PLENORA-ETL API"}

    from app.observability.health import gather_liveness, gather_readiness

    @app.get("/health")
    def health():
        payload = gather_liveness()
        return JSONResponse(content=payload.body, status_code=payload.status_code)

    @app.get("/ready")
    def ready():
        payload = gather_readiness()
        return JSONResponse(content=payload.body, status_code=payload.status_code)

    return app


__all__ = ["AppFactoryDeps", "create_app", "_compute_doc_endpoints"]
