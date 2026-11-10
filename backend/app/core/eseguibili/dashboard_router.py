"""Endpoint della dashboard Grafana degli Eseguibili (11/11/2026).

Gestione (stessa regola di modifica dell'Eseguibile: ``require_admin`` +
``assert_can_modify``):
- ``POST   /eseguibili/{id}/dashboard``            crea (201)
- ``DELETE /eseguibili/{id}/dashboard``            elimina (204)
- ``POST   /eseguibili/{id}/dashboard/edit-link``  sincronizza e da' il link

Esecuzione (chi vede l'Eseguibile e ha LANCIATO l'esecuzione, o superadmin):
- ``POST   /eseguibili/{id}/dashboard/pass``       lasciapassare dei dati

Dati per Infinity (chiamato da Grafana lato server, nessun Bearer):
- ``GET    /dash/exec/{task_id}/rows?pass=...``    intestazione
  ``X-Grafana-Secret`` obbligatoria, accesso deciso SOLO dal lasciapassare.

Errori: una tabella (``_ERRORS``) dalle eccezioni delle regole
(``eseguibili.dashboard``) ai codici del catalogo.
"""

from __future__ import annotations

import hmac
import threading
import time
from collections import deque
from typing import Mapping, NoReturn, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from app.core.auth.dependencies import get_current_user, require_admin
from app.core.auth.ownership import assert_can_modify, assert_resource_visible
from app.core.grafana import client as grafana_client
from app.core.grafana import settings as grafana_settings
from app.core.grafana import signing
from app.observability.audit_action import audit_action
from app.shared import error_codes
from app.shared.contracts.auth import CurrentUser
from app.shared.contracts.payloads import JsonObject
from app.shared.error_envelope import raise_typed_http
from app.utils.logger import get_logger

from . import dashboard as dashboard_rules
from . import dashboard_data
from . import db as eseguibili_db
from .models import (
    DashboardEditLinkRequest,
    DashboardInfo,
    DashboardPassRequest,
    DashboardPassResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/eseguibili", tags=["eseguibili"])
data_router = APIRouter(prefix="/dash", tags=["eseguibili"])


# Regola unica degli errori: eccezione -> (codice del catalogo, stato, messaggio).
_ERRORS: tuple[tuple[type[BaseException], str, int, Optional[str]], ...] = (
    (dashboard_rules.DashboardDisabled, error_codes.GRAFANA_DISABLED, 400, None),
    (grafana_client.GrafanaDisabled, error_codes.GRAFANA_DISABLED, 400, None),
    (dashboard_rules.DashboardMissing, error_codes.DASHBOARD_NOT_FOUND, 404, None),
    (dashboard_rules.DashboardExists, error_codes.DASHBOARD_ALREADY_EXISTS, 409, None),
    (dashboard_data.DashboardDataGone, error_codes.DASHBOARD_DATA_UNAVAILABLE, 410,
     "I dati per la dashboard di questa esecuzione non sono piu' disponibili"),
    (dashboard_rules.PassRejected, error_codes.DASHBOARD_PASS_INVALID, 401, "Lasciapassare non valido o scaduto"),
    (signing.SigningUnavailable, error_codes.GRAFANA_UNAVAILABLE, 503,
     "Dashboard non configurate su questo server (segreti mancanti)"),
    (grafana_client.GrafanaUnavailable, error_codes.GRAFANA_UNAVAILABLE, 503,
     "Grafana non risponde: riprova tra poco"),
    (grafana_client.GrafanaError, error_codes.GRAFANA_UNAVAILABLE, 502, "Grafana ha rifiutato la richiesta"),
    (ValueError, error_codes.VALIDATION_ERROR, 400, None),
)


def _raise_mapped(exc: BaseException) -> NoReturn:
    if isinstance(exc, dashboard_rules.TaskNotOwned):
        code = error_codes.ACCESS_DENIED if exc.status_code == 403 else error_codes.TASK_NOT_FOUND
        raise_typed_http(code, status_code=exc.status_code, message=str(exc))
    for exc_type, code, status, message in _ERRORS:
        if isinstance(exc, exc_type):
            if isinstance(exc, (grafana_client.GrafanaUnavailable, grafana_client.GrafanaError)):
                logger.warning(f"[dashboard] Grafana: {exc}")
            raise_typed_http(code, status_code=status, message=message or str(exc))
    raise exc


_HANDLED = (dashboard_rules.TaskNotOwned, *(entry[0] for entry in _ERRORS))


def _modifiable(eseguibile_id: str, user: CurrentUser) -> eseguibili_db.EseguibileRow:
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    assert_can_modify(record, user, "Eseguibile")
    return record


def _info(record: eseguibili_db.EseguibileRow) -> JsonObject:
    uid = str(record.dashboard_uid)
    return {
        "dashboard_uid": uid,
        "view_path": dashboard_rules.view_path(uid),
        "edit_path": dashboard_rules.view_path(uid),
        "dashboard_warnings": dashboard_rules.dashboard_warnings(record),
    }


@router.post("/{eseguibile_id}/dashboard", response_model=DashboardInfo, status_code=201)
@audit_action(action="eseguibile.dashboard.create", target_param="eseguibile_id")
def create_eseguibile_dashboard(
    eseguibile_id: str,
    current_user: CurrentUser = Depends(require_admin),
) -> Mapping[str, object]:
    """Genera la dashboard di partenza dalle colonne dell'output."""
    record = _modifiable(eseguibile_id, current_user)
    try:
        updated = dashboard_rules.create_dashboard(record, current_user)
    except _HANDLED as exc:
        _raise_mapped(exc)
    return _info(updated)


@router.delete("/{eseguibile_id}/dashboard", status_code=204, response_model=None)
@audit_action(action="eseguibile.dashboard.delete", target_param="eseguibile_id")
def delete_eseguibile_dashboard(
    eseguibile_id: str,
    current_user: CurrentUser = Depends(require_admin),
) -> None:
    """Elimina la dashboard (in Grafana e il legame)."""
    record = _modifiable(eseguibile_id, current_user)
    try:
        dashboard_rules.delete_dashboard(record)
    except _HANDLED as exc:
        _raise_mapped(exc)


@router.post("/{eseguibile_id}/dashboard/edit-link", response_model=DashboardInfo)
@audit_action(action="eseguibile.dashboard.edit_link", target_param="eseguibile_id")
def get_eseguibile_dashboard_edit_link(
    eseguibile_id: str,
    body: Optional[DashboardEditLinkRequest] = None,
    current_user: CurrentUser = Depends(require_admin),
) -> Mapping[str, object]:
    """«Modifica in Grafana»: sincronizza team e cartelle, poi il link."""
    record = _modifiable(eseguibile_id, current_user)
    try:
        return dashboard_rules.edit_link(record, current_user, task_id=(body.task_id if body else None))
    except _HANDLED as exc:
        _raise_mapped(exc)


@router.post("/{eseguibile_id}/dashboard/pass", response_model=DashboardPassResponse)
@audit_action(action="eseguibile.dashboard.pass", target_param="eseguibile_id", sample_reads=True)
def issue_eseguibile_dashboard_pass(
    eseguibile_id: str,
    payload: DashboardPassRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> Mapping[str, object]:
    """Lasciapassare dei dati di una esecuzione (rinnovabile finche' la
    finestra dei risultati e' aperta, mai oltre la scadenza dei dati)."""
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    assert_resource_visible(record, "eseguibili", current_user, "Eseguibile")
    try:
        return dashboard_rules.issue_pass(record, payload.task_id, current_user)
    except _HANDLED as exc:
        _raise_mapped(exc)


# ── Endpoint dati per Infinity ──────────────────────────────────────────────

class _TaskRateLimit:
    """Limite semplice per task (finestra di 60 s, nel processo)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str, *, per_minute: int, now: Optional[float] = None) -> bool:
        current = time.monotonic() if now is None else now
        with self._lock:
            window = self._hits.setdefault(key, deque())
            while window and window[0] <= current - 60:
                window.popleft()
            if len(window) >= per_minute:
                return False
            window.append(current)
            if len(self._hits) > 10_000:  # chiavi vecchie
                for stale in [k for k, v in self._hits.items() if not v]:
                    self._hits.pop(stale, None)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limit = _TaskRateLimit()


def _check_grafana_secret(provided: Optional[str]) -> None:
    expected = grafana_settings.data_secret()
    if not expected:
        raise_typed_http(error_codes.GRAFANA_UNAVAILABLE, status_code=503,
                         message="Endpoint dati non configurato (GRAFANA_DATA_SECRET)")
    if not provided or not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise_typed_http(error_codes.DASHBOARD_PASS_INVALID, status_code=401, message="Chiamante non autorizzato")


@data_router.get("/exec/{task_id}/rows")
def read_dashboard_rows(
    task_id: str,
    pass_token: Optional[str] = Query(default=None, alias="pass", max_length=signing.MAX_TOKEN_LENGTH),
    x_grafana_secret: Optional[str] = Header(default=None, alias="X-Grafana-Secret"),
) -> Response:
    """Righe della copia per la dashboard (JSON array di oggetti, al massimo
    ``DASHBOARD_DATA_MAX_ROWS``). Lettura non distruttiva. 401 lasciapassare
    non valido/scaduto/di altro task, 410 dati non piu' disponibili."""
    if not grafana_settings.enabled():
        raise_typed_http(error_codes.GRAFANA_DISABLED, status_code=404, message="Dashboard disabilitate")
    if not dashboard_rules.is_safe_task_id(task_id):
        raise HTTPException(status_code=400, detail="task_id non valido")
    _check_grafana_secret(x_grafana_secret)
    if not rate_limit.allow(task_id, per_minute=grafana_settings.data_rate_per_minute()):
        raise HTTPException(status_code=429, detail="Troppe richieste per questa esecuzione", headers={"Retry-After": "10"})
    try:
        body = dashboard_rules.read_rows(task_id, pass_token)
    except _HANDLED as exc:
        _raise_mapped(exc)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


__all__ = [
    "create_eseguibile_dashboard",
    "data_router",
    "delete_eseguibile_dashboard",
    "get_eseguibile_dashboard_edit_link",
    "issue_eseguibile_dashboard_pass",
    "rate_limit",
    "read_dashboard_rows",
    "router",
]
