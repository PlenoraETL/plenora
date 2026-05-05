"""
PLENORA-ETL — Middleware JWT

Intercetta ogni richiesta HTTP, legge il token Bearer se presente,
e imposta il group_id nel contesto del thread per il logger.

NON blocca le richieste senza token (quello lo fa la dependency get_current_user).
Serve solo a propagare il group_id al logger automaticamente.

SECURITY:
  - Token scaduti: `decode_token` ritorna None → middleware setta TUTTI i
    campi `request.state.*` a stringa vuota. Quindi un endpoint che legge
    `request.state.group_id` su un token scaduto vede "".
  - Tutti gli endpoint protetti DEVONO usare `Depends(get_current_user)`
    o equivalenti — quella dependency rifiuta esplicitamente token mancanti
    o invalidi con 401. Non basarsi MAI solo su `request.state.role` per
    autorizzare azioni: usare `current_user: CurrentUser = Depends(...)`.

Endpoint pubblici (no auth):
  - POST /auth/login
  - GET  /docs, /redoc, /openapi.json
  - GET  /health
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.shared.jwt_token import decode_token
from app.utils.logger import set_log_group, set_user_id, get_logger

logger = get_logger(__name__)


PUBLIC_PATHS = {
    "/auth/login",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/health",
}


def _clear_request_state(request: Request) -> None:
    """Imposta tutti i campi request.state a stringa vuota (utente anonimo).

    NB: il reset dei ContextVar (set_log_group, set_user_id) va fatto
    esplicitamente dal caller — questo helper tocca solo request.state.
    """
    request.state.group_id = ""
    request.state.user_id = ""
    request.state.username = ""
    request.state.role = ""


class JWTMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Imposta group_id dal token se presente (non blocca se assente)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            payload = decode_token(token)
            if payload:
                group_id = payload.get("group_id", "")
                user_id = payload.get("sub", "")
                set_log_group(group_id)
                set_user_id(user_id)  # C8: correlation user_id nei log
                # Inietta nel request.state per uso negli endpoint
                request.state.group_id = group_id
                request.state.user_id = user_id
                request.state.username = payload.get("username", "")
                request.state.role = payload.get("role", "user")
            else:
                # Token presente ma decoda fallita (scaduto/invalido/firma errata).
                # Logghiamo per visibility — utile per detection di replay
                # attacks o token stale persistenti nei client.
                logger.warning(
                    "Bearer token invalido o scaduto",
                    op="auth_middleware",
                    details={"path": str(request.url.path)},
                )
                set_log_group("")
                set_user_id("")  # C8-nit: evita leak user_id da richieste precedenti
                _clear_request_state(request)
        else:
            set_log_group("")
            set_user_id("")  # C8-nit: evita leak user_id da richieste precedenti
            _clear_request_state(request)

        response = await call_next(request)
        return response
