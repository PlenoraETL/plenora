"""Accesso a ``/grafana`` per i soli utenti Plenora (11/11/2026).

Caddy protegge ``/grafana/*`` con ``forward_auth`` verso
``GET /api/grafana/auth``; niente accesso anonimo.

- ``POST   /api/grafana/session`` (Bearer): imposta il cookie dedicato
  ``plenora_grafana_session`` (HttpOnly, SameSite=Strict, Path=/grafana,
  firmato con ``DASHBOARD_PASS_SECRET`` nel dominio «sessione»: non e' il JWT
  di login), durata ``GRAFANA_SESSION_TTL_SECONDS``; si rinnova richiamandolo.
- ``GET    /api/grafana/auth`` (forward_auth): 200 con ``X-WEBAUTH-USER`` (e
  ``X-WEBAUTH-ROLE: Admin`` solo per il superadmin; per gli altri NESSUN
  ruolo: Grafana li crea Viewer); 401 se il cookie manca, e' malformato o
  scaduto, o l'utente e' disattivato o eliminato. Nome e ruolo si rileggono
  dal DB a ogni richiesta (``auth.db.get_user``: mai una lapide).
- ``DELETE /api/grafana/session``: toglie il cookie.
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Request, Response

from app.core.auth import db as users_db
from app.core.auth.dependencies import get_current_user
from app.core.grafana import settings as grafana_settings
from app.core.grafana import signing
from app.observability.audit_action import audit_action
from app.shared import error_codes
from app.shared.contracts.auth import CurrentUser
from app.shared.error_envelope import raise_typed_http

SESSION_COOKIE = "plenora_grafana_session"
COOKIE_PATH = "/grafana"
USER_HEADER = "X-WEBAUTH-USER"
ROLE_HEADER = "X-WEBAUTH-ROLE"

grafana_router = APIRouter(prefix="/grafana", tags=["grafana"])


def _secure(request: Request) -> bool:
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return forwarded == "https" or request.url.scheme == "https"


@grafana_router.post("/session", status_code=204, response_model=None)
@audit_action(action="grafana.session", sample_reads=True)
def open_grafana_session(
    request: Request,
    response: Response,
    current_user: CurrentUser = Depends(get_current_user),
) -> None:
    """Cookie per /grafana (iframe della dashboard, «Modifica in Grafana»)."""
    if not grafana_settings.enabled():
        raise_typed_http(error_codes.GRAFANA_DISABLED, status_code=400,
                         message="Le dashboard Grafana sono disabilitate su questo server")
    ttl = grafana_settings.session_ttl_seconds()
    try:
        token = signing.issue_session(user_id=current_user.user_id, expires_at=int(time.time()) + ttl)
    except signing.SigningUnavailable:
        raise_typed_http(error_codes.GRAFANA_UNAVAILABLE, status_code=503,
                         message="Dashboard non configurate su questo server (segreti mancanti)")
    response.set_cookie(
        SESSION_COOKIE, token, max_age=ttl, path=COOKIE_PATH, httponly=True, samesite="strict",
        secure=_secure(request),
    )


@grafana_router.delete("/session", status_code=204, response_model=None)
def close_grafana_session(request: Request, response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path=COOKIE_PATH, httponly=True, samesite="strict",
                           secure=_secure(request))


@grafana_router.get("/auth", status_code=200, response_model=None)
def grafana_forward_auth(
    plenora_grafana_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
) -> Response:
    """forward_auth di Caddy per ogni richiesta a /grafana/*."""
    unauthorized = Response(status_code=401, headers={"Cache-Control": "no-store"})
    if not grafana_settings.enabled():
        return unauthorized
    try:
        session = signing.verify_session(plenora_grafana_session)
    except (signing.InvalidToken, signing.SigningUnavailable):
        return unauthorized
    user = users_db.get_user(session.user_id)
    if user is None or not user.is_active or getattr(user, "deleted_at", None):
        return unauthorized
    headers = {USER_HEADER: str(user.username), "Cache-Control": "no-store"}
    if user.role == "superadmin":
        headers[ROLE_HEADER] = "Admin"
    return Response(status_code=200, headers=headers)


__all__ = [
    "COOKIE_PATH",
    "ROLE_HEADER",
    "SESSION_COOKIE",
    "USER_HEADER",
    "close_grafana_session",
    "grafana_forward_auth",
    "grafana_router",
    "open_grafana_session",
]
