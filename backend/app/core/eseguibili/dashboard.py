"""Dashboard Grafana di un Eseguibile: regole (11/11/2026).

Raccolta: ``docs/audits/grafana-dashboard-raccolta-2026-11-11.md``.

- **Una dashboard per Eseguibile** (``eseguibili.dashboard_uid``), generata
  dalle colonne attese (``expected_output_columns``); ripubblicare non la
  tocca; eliminare l'Eseguibile la elimina in Grafana (best effort).
- **Chi la modifica**: chi puo' modificare l'Eseguibile (``require_admin`` +
  ``assert_can_modify`` nel router). In Grafana la stessa regola: la cartella
  si sceglie con ``compute_can_edit`` per un admin del gruppo (Eseguibile
  creato da un superadmin -> cartella riservata, senza team).
- **Chi vede i dati**: solo con il lasciapassare di UNA esecuzione, emesso a
  chi l'ha lanciata (o al superadmin, come il download); i dati sono la
  copia di ``dashboard_data`` (30 minuti, 50.000 righe).
- **Grafana spento** (``GRAFANA_ENABLED=false``): ``client.get_client``
  rifiuta prima di qualunque connessione; qui ogni operazione lo controlla
  per prima (``DashboardDisabled``).
"""

from __future__ import annotations

import re
import time
from typing import Any, Iterable, List, Mapping, Optional
from urllib.parse import urlencode

from app.core.auth import db as users_db
from app.core.auth.ownership import compute_can_edit
from app.core.grafana import client as grafana_client
from app.core.grafana import dashboard_builder
from app.core.grafana import settings as grafana_settings
from app.core.grafana import signing
from app.core.grafana import sync as grafana_sync
from app.core.projects.output_contract_service import ProjectOutputContractService
from app.shared.contracts.auth import CurrentUser
from app.utils.logger import get_logger

from . import dashboard_data, output_store
from . import db as eseguibili_db

logger = get_logger(__name__)

_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_USERS_PAGE = 500


class DashboardDisabled(RuntimeError):
    """``GRAFANA_ENABLED=false``."""


class DashboardMissing(LookupError):
    """L'Eseguibile non ha una dashboard."""


class DashboardExists(RuntimeError):
    """L'Eseguibile ha gia' una dashboard."""


class TaskNotOwned(PermissionError):
    """L'esecuzione e' di un altro utente (o di un altro Eseguibile)."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class PassRejected(PermissionError):
    """Lasciapassare non valido per questa lettura (401)."""


def assert_enabled() -> None:
    if not grafana_settings.enabled():
        raise DashboardDisabled("Le dashboard Grafana sono disabilitate su questo server (GRAFANA_ENABLED=false)")


def is_safe_task_id(task_id: object) -> bool:
    return isinstance(task_id, str) and bool(_SAFE_TASK_ID.match(task_id)) and ".." not in task_id


# ── Colonne attese ──────────────────────────────────────────────────────────

def expected_output_columns(record: Any) -> Optional[List[dashboard_builder.OutputColumnSpec]]:
    """Colonne attese dell'output: l'``output_contract`` del progetto
    sorgente (ultimo schema pubblicato dal progetto). None = non si sa."""
    project_id = getattr(record, "source_canvas_id", None)
    if not project_id:
        return None
    try:
        contract = ProjectOutputContractService().get_project_output_contract(str(project_id))
    except Exception:
        logger.warning(f"[dashboard] output_contract di {project_id} non leggibile", exc_info=True)
        return None
    columns = contract.get("columns") if isinstance(contract, Mapping) else None
    if not isinstance(columns, list) or not columns:
        return None
    ordered = sorted((c for c in columns if isinstance(c, Mapping)), key=lambda c: int(c.get("ordinal") or 0))
    specs = []
    for column in ordered:
        name = str(column.get("physical_name") or column.get("display_name") or "")
        column_type = column.get("type")
        logical = str(column_type.get("logical") or "string") if isinstance(column_type, Mapping) else "string"
        if name:
            specs.append(dashboard_builder.OutputColumnSpec(name=name, logical=logical))
    return specs or None


# ── Percorsi ────────────────────────────────────────────────────────────────

def view_path(uid: str) -> str:
    return f"{grafana_settings.public_path()}/d/{uid}?orgId=1"


def _with_data(path: str, task_id: Optional[str], token: Optional[str]) -> str:
    if not task_id or not token:
        return path
    return f"{path}&{urlencode({'var-exec': task_id, 'var-pass': token})}"


def iframe_path(uid: str, task_id: str, token: str) -> str:
    return _with_data(f"{grafana_settings.public_path()}/d/{uid}?orgId=1&kiosk", task_id, token)


# ── Grafana: cartella e sincronizzazione ────────────────────────────────────

def _admin_can_modify(record: Any) -> bool:
    """La STESSA regola dell'Eseguibile (``ownership.compute_can_edit``) per un
    admin qualunque del gruppo proprietario."""
    probe = CurrentUser(user_id="", username="", group_id=str(record.group_id), role="admin")
    return compute_can_edit(record, probe)


def folder_for(record: Any) -> str:
    return dashboard_builder.folder_uid(str(record.group_id), reserved=not _admin_can_modify(record))


def _plenora(row: Any) -> grafana_sync.PlenoraUser:
    return grafana_sync.PlenoraUser(
        username=str(row.username), role=str(row.role), group_id=str(row.group_id), active=bool(row.is_active),
    )


def _all_users() -> Iterable[grafana_sync.PlenoraUser]:
    offset = 0
    while True:
        page = users_db.list_all_users(limit=_USERS_PAGE, offset=offset)
        for row in page:
            yield _plenora(row)
        if len(page) < _USERS_PAGE:
            return
        offset += _USERS_PAGE


def sync_group_for(record: Any, client: grafana_client.GrafanaApi, requester: Optional[CurrentUser] = None) -> None:
    """Cartelle, team e ruoli del gruppo proprietario (idempotente)."""
    group_id = str(record.group_id)
    group = users_db.get_group(group_id)
    group_users = [_plenora(row) for row in users_db.list_users(group_id, limit=10_000)]
    requester_user = (
        grafana_sync.PlenoraUser(requester.username, requester.role, requester.group_id) if requester else None
    )
    grafana_sync.sync_group(
        client,
        group_id=group_id,
        group_title=str(getattr(group, "name", "") or group_id),
        group_users=group_users,
        all_users=list(_all_users()),
        requester=requester_user,
    )


# ── Operazioni ──────────────────────────────────────────────────────────────

def create_dashboard(record: Any, requester: CurrentUser) -> Any:
    """Genera la dashboard di partenza e la lega all'Eseguibile."""
    assert_enabled()
    if record.dashboard_uid:
        raise DashboardExists(f"L'Eseguibile ha gia' una dashboard ({record.dashboard_uid})")
    client = grafana_client.get_client()
    uid = dashboard_builder.dashboard_uid(record.id)
    dashboard = dashboard_builder.build_dashboard(
        uid=uid,
        title=str(record.name),
        columns=expected_output_columns(record) or [],
        data_url=grafana_settings.data_url(),
        datasource_uid=grafana_settings.datasource_uid(),
    )
    sync_group_for(record, client, requester)
    client.upsert_dashboard(dashboard, folder_uid=folder_for(record), message="generata da Plenora")
    updated = eseguibili_db.set_dashboard_uid(record.id, uid)
    return updated if updated is not None else record


def delete_dashboard(record: Any) -> Any:
    """Elimina la dashboard (Grafana 404 = gia' eliminata) e la slega."""
    assert_enabled()
    if not record.dashboard_uid:
        raise DashboardMissing("L'Eseguibile non ha una dashboard")
    grafana_client.get_client().delete_dashboard(str(record.dashboard_uid))
    updated = eseguibili_db.set_dashboard_uid(record.id, None)
    return updated if updated is not None else record


def delete_dashboard_best_effort(dashboard_uid: Optional[str]) -> None:
    """Eliminazione dell'Eseguibile: mai bloccarla se Grafana non risponde."""
    if not dashboard_uid or not grafana_settings.enabled():
        return
    try:
        grafana_client.get_client().delete_dashboard(str(dashboard_uid))
    except Exception:
        logger.warning(
            f"[dashboard] eliminazione della dashboard {dashboard_uid} in Grafana non riuscita: resta in Grafana",
            exc_info=True,
        )


def edit_link(record: Any, requester: CurrentUser, *, task_id: Optional[str] = None) -> Mapping[str, object]:
    """«Modifica in Grafana»: sincronizza (team, cartella, utente) e
    restituisce il link; con ``task_id`` anche i dati di quell'esecuzione."""
    assert_enabled()
    if not record.dashboard_uid:
        raise DashboardMissing("L'Eseguibile non ha una dashboard")
    uid = str(record.dashboard_uid)
    client = grafana_client.get_client()
    sync_group_for(record, client, requester)
    entry = client.get_dashboard(uid)
    if entry is None:
        raise DashboardMissing("La dashboard non esiste piu' in Grafana: eliminala e ricreala")
    current = entry["dashboard"]
    # La cartella segue la regola anche se il creatore e' cambiato.
    folder = folder_for(record)
    if entry.get("folder_uid") != folder:
        client.upsert_dashboard(current, folder_uid=folder, message="cartella sincronizzata da Plenora")
    token = None
    if task_id:
        token = issue_pass(record, task_id, requester)["pass_token"]
    return {
        "dashboard_uid": uid,
        "view_path": view_path(uid),
        "edit_path": _with_data(view_path(uid), task_id, token),
        "dashboard_warnings": dashboard_warnings(record, dashboard=current),
    }


def dashboard_warnings(record: Any, *, dashboard: Optional[Mapping[str, Any]] = None) -> List[Mapping[str, object]]:
    """Colonne usate dalla dashboard che non sono tra le colonne attese."""
    if not getattr(record, "dashboard_uid", None) or not grafana_settings.enabled():
        return []
    if dashboard is None:
        try:
            entry = grafana_client.get_client().get_dashboard(str(record.dashboard_uid))
        except (grafana_client.GrafanaUnavailable, grafana_client.GrafanaError, grafana_client.GrafanaDisabled):
            return [{"column": None, "motivo": "Dashboard non verificabile: Grafana non risponde"}]
        if entry is None:
            return [{"column": None, "motivo": "La dashboard non esiste piu' in Grafana: eliminala e ricreala"}]
        dashboard = entry["dashboard"]
    expected = expected_output_columns(record)
    if expected is None:
        return []
    return [
        {"column": name, "motivo": f"La dashboard usa la colonna «{name}», che la versione attiva non produce piu'"}
        for name in dashboard_builder.missing_columns(dashboard, [col.name for col in expected])
    ]


# ── Lasciapassare e dati ────────────────────────────────────────────────────

def _owner_of(task_id: str, record: Any) -> Mapping[str, object]:
    owner = output_store.load_task_owner(task_id, group_id=str(record.group_id))
    if owner is None:
        raise dashboard_data.DashboardDataGone(task_id)
    return owner


def issue_pass(record: Any, task_id: str, user: CurrentUser, *, now: Optional[float] = None) -> Mapping[str, object]:
    """Lasciapassare dei dati di UNA esecuzione: solo a chi l'ha lanciata
    (``owner_meta.user_id``) o al superadmin, come il download."""
    assert_enabled()
    if not record.dashboard_uid:
        raise DashboardMissing("L'Eseguibile non ha una dashboard")
    if not is_safe_task_id(task_id):
        raise ValueError("task_id non valido")
    owner = _owner_of(task_id, record)
    if owner.get("eseguibile_id") != record.id:
        raise TaskNotOwned("Esecuzione di un altro Eseguibile", status_code=404)
    if owner.get("user_id") != user.user_id and user.role != "superadmin":
        raise TaskNotOwned("Dati non accessibili (esecuzione di un altro utente)", status_code=403)
    current = time.time() if now is None else now
    _key, data_expires_at = dashboard_data.current_key(task_id, group_id=str(record.group_id), now=current)
    expires_at = min(int(current) + grafana_settings.pass_ttl_seconds(), int(data_expires_at))
    token = signing.issue_pass(task_id=task_id, eseguibile_id=str(record.id), user_id=user.user_id,
                               expires_at=expires_at)
    uid = str(record.dashboard_uid)
    return {
        "task_id": task_id,
        "dashboard_uid": uid,
        "pass_token": token,
        "expires_at": expires_at,
        "data_expires_at": int(data_expires_at),
        "iframe_path": iframe_path(uid, task_id, token),
    }


def read_rows(task_id: str, token: Optional[str], *, now: Optional[float] = None) -> bytes:
    """Endpoint dati: la decisione d'accesso e' SOLO il lasciapassare."""
    assert_enabled()
    if not is_safe_task_id(task_id):
        raise ValueError("task_id non valido")
    try:
        granted = signing.verify_pass(token, now=now)
    except signing.InvalidToken as exc:
        raise PassRejected(f"lasciapassare non valido ({exc})") from exc
    if granted.task_id != task_id:
        raise PassRejected("lasciapassare di un'altra esecuzione")
    user = users_db.get_user(granted.user_id)
    if user is None or not user.is_active:
        raise PassRejected("utente del lasciapassare non attivo")
    record = eseguibili_db.get_eseguibile(granted.eseguibile_id)
    if record is None:
        raise dashboard_data.DashboardDataGone(task_id)
    owner = _owner_of(task_id, record)
    if owner.get("eseguibile_id") != record.id:
        raise PassRejected("esecuzione di un altro Eseguibile")
    if owner.get("user_id") != user.id and user.role != "superadmin":
        raise PassRejected("esecuzione di un altro utente")
    return dashboard_data.read(task_id, group_id=str(record.group_id), now=now)


__all__ = [
    "DashboardDisabled",
    "DashboardExists",
    "DashboardMissing",
    "PassRejected",
    "TaskNotOwned",
    "assert_enabled",
    "create_dashboard",
    "dashboard_warnings",
    "delete_dashboard",
    "delete_dashboard_best_effort",
    "edit_link",
    "expected_output_columns",
    "folder_for",
    "iframe_path",
    "is_safe_task_id",
    "issue_pass",
    "read_rows",
    "sync_group_for",
    "view_path",
]
