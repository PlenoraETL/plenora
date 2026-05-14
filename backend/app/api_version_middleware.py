"""
PLENORA-ETL — Middleware X-API-Version.

Setta `X-API-Version: <semver>` su ogni response (incluse le 4xx/5xx).
Il valore arriva da `app.config.API_VERSION`, bumpato esplicitamente al
cambio breaking del contratto FE↔BE. Il FE confronta contro la version
con cui e' stato compilato e logga warning su mismatch.

Vive nel middleware stack accanto a RequestIdMiddleware: stesso pattern
(scrittura header sulla response in uscita, niente input parsing).
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import API_VERSION


_HEADER = "X-API-Version"


class ApiVersionMiddleware(BaseHTTPMiddleware):
    """Aggiunge l'header X-API-Version su ogni response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers[_HEADER] = API_VERSION
        return response
