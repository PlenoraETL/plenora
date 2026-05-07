"""
PLENORA-ETL — Middleware correlation X-Request-ID (C8).

Propaga un identificatore di richiesta lungo tutta la catena logging:
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.utils.logger import set_request_id, get_logger


logger = get_logger(__name__)

_HEADER = "X-Request-ID"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Genera/propaga un request_id per ogni richiesta HTTP."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Legge il request_id dal client se presente, altrimenti genera
        # Trust-but-verify: accettiamo qualsiasi stringa fornita dal
        # client (consente correlation con sistemi upstream) ma la
        # tronchiamo a 64 char per evitare log bombing
        incoming = request.headers.get(_HEADER, "").strip()
        req_id = incoming[:64] if incoming else str(uuid.uuid4())

        set_request_id(req_id)
        request.state.request_id = req_id

        try:
            response = await call_next(request)
        except Exception:
            # Anche su eccezione vogliamo reset ContextVar (evita leak
            # all'ambiente successivo via recycling worker). Starlette
            # comunque rigenera il contesto per la prossima request
            # ma esplicitare e' difensivo
            set_request_id("")
            raise

        response.headers[_HEADER] = req_id
        # Non resetto la ContextVar qui: la prossima request fara'
        # set_request_id sovrascrivendo. Il reset esplicito sopra
        # copre solo il caso eccezione propagata
        return response
