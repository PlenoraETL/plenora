"""
PLENORA-ETL — Middleware JWT

Intercetta ogni richiesta HTTP, legge il token Bearer se presente,
e propaga `group_id` / `user_id` ai ContextVar del logger
(`set_log_group`, `set_user_id`).

NON blocca le richieste senza token: quel ruolo e' della
dependency FastAPI `get_current_user`. Questo middleware serve
ESCLUSIVAMENTE a propagare la correlazione di logging.

BE-GO-READY-SECURITY-ENGINE-002-AUTH-CLEANUP-RESIDUALS:
  Le 4 scritture su `request.state.{group_id,user_id,username,role}`
  sono state rimosse: nessun runtime consumer le leggeva piu' (auth
  scorre via `Depends(get_current_user)` che ricostruisce il
  CurrentUser dal JWT, non dal request.state). I ContextVar restano
  l'unica superficie canonica di correlazione lato logging.
  `request.state.request_id` (RequestIdMiddleware) NON e' impattato.

SECURITY:
  - Tutti gli endpoint protetti DEVONO usare `Depends(get_current_user)`
    o equivalenti — quella dependency rifiuta esplicitamente token
    mancanti o invalidi con 401.
  - Non basarsi MAI su `request.state.*` per autorizzare azioni:
    usare `current_user: CurrentUser = Depends(...)`.
  - I ContextVar `log_group` / `user_id` servono solo a
    correlazione log, non hanno alcuna valenza di authorization.

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


class JWTMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Imposta group_id / user_id nei ContextVar del logger se il
        # token e' presente e valido (non blocca su assenza/invalidita':
        # quel ruolo e' della dependency `get_current_user`).
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            payload = decode_token(token)
            if payload:
                set_log_group(payload.get("group_id", ""))
                set_user_id(payload.get("sub", ""))  # C8: correlation user_id
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
        else:
            set_log_group("")
            set_user_id("")  # C8-nit: evita leak user_id da richieste precedenti

        response = await call_next(request)
        return response
