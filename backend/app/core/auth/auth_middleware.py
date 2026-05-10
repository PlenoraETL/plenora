"""
PLENORA-ETL — Middleware JWT

Intercetta ogni richiesta HTTP, legge il token Bearer se presente,
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.auth.jwt_token import decode_token
from app.utils.logger import set_log_group, set_user_id, get_logger

logger = get_logger(__name__)


PUBLIC_PATHS = {
    "/auth/login",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/health",
}


class JWTMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Imposta group_id / user_id nei ContextVar del logger se il
        # token e' presente e valido (non blocca su assenza/invalidita'
        # quel ruolo e' della dependency `get_current_user`
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            payload = decode_token(token)
            if payload:
                set_log_group(payload.get("group_id", ""))
                set_user_id(payload.get("sub", ""))  # C8: correlation user_id
            else:
                # Token presente ma decoda fallita (scaduto/invalido/firma errata
                # Logghiamo per visibility — utile per detection di replay
                # attacks o token stale persistenti nei client
                logger.warning(
                    "Bearer token invalido o scaduto",
                    op="auth_middleware",
                    details={"path": str(request.url.path)},
                )
                set_log_group("")
                set_user_id("")  # C8-nit: evita leak user_id da richieste precedenti
        else:
            set_log_group("")
            set_user_id("")  # C8-nit: evita leak user_id da richieste precedenti

        response = await call_next(request)
        return response
