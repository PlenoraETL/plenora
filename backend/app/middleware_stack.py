"""FastAPI middleware stack assembly.

FastAPI executes middleware in reverse registration order. Keeping the order in
one function makes the runtime chain explicit and avoids scattering fragile
ordering notes through `main.py`.
"""

from __future__ import annotations

from typing import Any

from fastapi.middleware.cors import CORSMiddleware

from app.api_version_middleware import ApiVersionMiddleware
from app.core.auth.auth_middleware import JWTMiddleware
from app.core.auth.request_id_middleware import RequestIdMiddleware
from app.db.cleanup_middleware import DBConnectionCleanupMiddleware


def install_middleware_stack(app: Any, *, cors_origins: list[str]) -> None:
    """Install the canonical outer-to-inner request middleware chain."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Accept",
            "X-Requested-With",
            "X-Request-ID",
        ],
        expose_headers=["Content-Disposition", "X-Request-ID", "X-API-Version"],
    )
    app.add_middleware(JWTMiddleware)
    app.add_middleware(DBConnectionCleanupMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(ApiVersionMiddleware)


__all__ = ["install_middleware_stack"]
