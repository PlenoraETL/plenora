"""Accesso a ``/grafana`` per i soli utenti Plenora (11/11/2026).

Caddy protegge ``/grafana/*`` con ``forward_auth`` verso
``GET /api/grafana/auth``; niente accesso anonimo.

- ``POST   /api/grafana/session`` (Bearer): imposta il cookie dedicato
  ``plenora_grafana_session`` (HttpOnly, SameSite=Strict, Path=/grafana,
  firmato con ``DASHBOARD_PASS_SECRET`` nel dominio «sessione»: non e' il JWT
  di login), durata ``GRAFANA_SESSION_TTL_SECONDS``; si rinnova richiamandolo.
- ``GET    /api/grafana/auth`` (forward_auth): 200 con le intestazioni
  auth-proxy di ``grafana.sync.proxy_headers`` (correzione 11/11/2026):
  ``X-WEBAUTH-USER`` = login legato all'ID (``plenora-u-<id>``, mai lo
  username: uno username riusato non eredita nulla), ``X-WEBAUTH-NAME`` =
  nome visualizzato, ``X-WEBAUTH-ROLE`` SEMPRE (Admin al superadmin, Viewer
  a tutti gli altri: Grafana riallinea il ruolo a ogni accesso). Prima di
  rispondere allinea i team dell'utente in Grafana
  (``grafana.access.ensure_access``, cache per utente); Grafana che non
  risponde -> 503, mai un passaggio con team non verificati. 401 se il
  cookie manca, e' malformato o scaduto, o l'utente e' disattivato o
  eliminato. L'utente si rilegge dal DB a ogni richiesta
  (``auth.db.get_user``: mai una lapide).
- ``DELETE /api/grafana/session``: toglie il cookie.
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Request, Response

from app.core.auth import db as users_db
from app.core.auth.dependencies import get_current_user
from app.core.grafana import access as grafana_access
from app.core.grafana import client as grafana_client
from app.core.grafana import settings as grafana_settings
from app.core.grafana import signing
from app.core.grafana import sync as grafana_sync
from app.observability.audit_action import audit_action
from app.shared import error_codes
from app.shared.contracts.auth import CurrentUser
from app.shared.error_envelope import raise_typed_http
from app.utils.logger import get_logger

logger = get_logger(__name__)

SESSION_COOKIE = "plenora_grafana_session"
COOKIE_PATH = "/grafana"
USER_HEADER = grafana_sync.USER_HEADER
NAME_HEADER = grafana_sync.NAME_HEADER
ROLE_HEADER = grafana_sync.ROLE_HEADER

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
    plenora = grafana_sync.PlenoraUser(
        user_id=str(user.id), username=str(user.username), role=str(user.role),
        group_id=str(user.group_id or ""), active=bool(user.is_active),
    )
    try:
        grafana_access.ensure_access(plenora)
    except (grafana_client.GrafanaUnavailable, grafana_client.GrafanaError, grafana_client.GrafanaDisabled):
        logger.warning(f"[grafana] accesso di {user.id} non allineato in Grafana: 503", exc_info=True)
        return Response(status_code=503, headers={"Cache-Control": "no-store", "Retry-After": "5"})
    return Response(status_code=200, headers={**grafana_sync.proxy_headers(plenora), "Cache-Control": "no-store"})


__all__ = [
    "COOKIE_PATH",
    "NAME_HEADER",
    "ROLE_HEADER",
    "SESSION_COOKIE",
    "USER_HEADER",
    "close_grafana_session",
    "grafana_forward_auth",
    "grafana_router",
    "open_grafana_session",
]
