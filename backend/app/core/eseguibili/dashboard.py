"""Dashboard Grafana di un Eseguibile: regole (11/11/2026).

Raccolta: ``docs/audits/grafana-dashboard-raccolta-2026-11-11.md``.

- **Una dashboard per Eseguibile** (``eseguibili.dashboard_uid``), generata
  dalle colonne attese (``expected_output_columns``); ripubblicare non la
  tocca; eliminare l'Eseguibile la elimina in Grafana (best effort).
- **Chi la modifica**: chi puo' modificare l'Eseguibile (``require_admin`` +
  ``assert_can_modify`` nel router). In Grafana la stessa regola: una
  cartella per Eseguibile, Edit al team ``admin-<gruppo>`` solo se
  ``compute_can_edit`` lo concede a un admin del gruppo (un Eseguibile creato
  da un superadmin non lo concede).
- **Chi la vede in Grafana** (correzione 11/11/2026): chi vede l'Eseguibile
  in Plenora, e nessun altro (``folder_access``: team ``utenti-<g>`` dei
  gruppi proprietario e condivisi, ruolo Viewer solo con ``*``). Le
  condivisioni che cambiano riallineano la cartella (``on_shares_changed``,
  registrata in ``auth.share_hooks``); ruolo e team di ogni utente si
  riallineano al forward_auth (``grafana.access``).
- **Chi vede i dati**: solo con il lasciapassare di UNA esecuzione, emesso a
  chi l'ha lanciata (o al superadmin, come il download); i dati sono la
  copia di ``dashboard_data`` (30 minuti, 50.000 righe).
- **Grafana spento** (``GRAFANA_ENABLED=false``): ``client.get_client``
  rifiuta prima di qualunque connessione; qui ogni operazione lo controlla
  per prima (``DashboardDisabled``).
- **Riallineamento** (correzioni minori 11/11/2026, B3/B4): UNA funzione,
  :func:`sync_eseguibile`, per creazione, «Modifica in Grafana», modifica
  delle condivisioni, cancellazione di un gruppo (:func:`on_group_deleted`:
  team ``utenti-<g>``/``admin-<g>`` eliminati, cartelle condivise
  riallineate) e riallineamento periodico del beat
  (:func:`resync_all`, ogni ``GRAFANA_FOLDER_RESYNC_INTERVAL_SECONDS``, solo
  con Grafana attivo): idempotente, chiude anche le modifiche fatte mentre
  Grafana era giu' e i team di gruppi che non esistono piu'. Un gruppo che
  non esiste piu' non entra mai in una cartella (:func:`folder_access`).
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional
from urllib.parse import urlencode

from app.core.auth import db as users_db
from app.core.auth import share_hooks
from app.core.auth.ownership import compute_can_edit
from app.core.auth.shares import list_resource_shares
from app.core.auth.visibility import MAGIC_ALL
from app.core.grafana import access as grafana_access
from app.core.grafana import client as grafana_client
from app.core.grafana import dashboard_builder
from app.core.grafana import settings as grafana_settings
from app.core.grafana import signing
from app.core.grafana import sync as grafana_sync
from app.core.projects.output_contract_service import ProjectOutputContractService
from app.shared.contracts.auth import CurrentUser
from app.shared.execution.runtime_identity import identity_source_column
from app.shared.execution.runtime_input_kinds import is_server_injected
from app.utils.logger import get_logger

from . import dashboard_data, output_store, upstream_plan
from .manifest import compile_project_inputs
from . import db as eseguibili_db

logger = get_logger(__name__)

_SAFE_TASK_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_GROUP_USERS_MAX = 10_000


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
    return isinstance(task_id, str) and bool(_SAFE_TASK_ID.fullmatch(task_id)) and ".." not in task_id


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


# Parametri del kiosk dell'iframe (B1): niente controlli del tempo, anche
# per le dashboard create prima di ``timepicker.hidden`` (Grafana 13).
KIOSK_PARAMS = "kiosk&_dash.hideTimePicker"


def iframe_path(uid: str, task_id: str, token: str) -> str:
    return _with_data(f"{grafana_settings.public_path()}/d/{uid}?orgId=1&{KIOSK_PARAMS}", task_id, token)


# ── Colonne legate all'«Utente che esegue» (grafici di partenza, B2) ───────

def _parent_column(node: Mapping[str, Any], path: str) -> Optional[str]:
    """La colonna dell'oggetto che contiene il campo collegato (es.
    ``filters.0.value`` -> ``filters[0].column``; ``config.value`` ->
    ``config.column``), nel nodo o nei suoi ``data``."""
    parts = str(path).split(".")[:-1]
    for root in (node.get("data"), node):
        current: Any = root
        for part in parts:
            if isinstance(current, Mapping):
                current = current.get(part)
            elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            else:
                current = None
                break
        if isinstance(current, Mapping) and isinstance(current.get("column"), str) and current["column"]:
            return str(current["column"])
    return None


def _canvas_identity_columns(canvas: Any, project_type: str) -> set[str]:
    if not isinstance(canvas, Mapping):
        return set()
    try:
        inputs, bindings = compile_project_inputs(canvas, project_type)  # type: ignore[arg-type]
    except ValueError:
        return set()
    injected = {item.key for item in inputs if is_server_injected(item.kind)}
    nodes = {str(n.get("id")): n for n in canvas.get("nodes") or [] if isinstance(n, Mapping)}
    columns: set[str] = set()
    for binding in bindings:
        node = nodes.get(str(binding.target_node_id))
        if binding.input_key not in injected or node is None:
            continue
        if binding.mode == "data_source":
            columns.add(identity_source_column(node))
        elif binding.target_field:
            column = _parent_column(node, binding.target_field)
            if column:
                columns.add(column)
    return columns


def identity_columns(record: Any) -> frozenset[str]:
    """Colonne filtrate da un input «Utente che esegue» del progetto e dei
    progetti a monte che ne hanno bisogno (la stessa chiusura dell'avvio):
    per chi esegue hanno un solo valore, mai categoria di un grafico."""
    project_type = str(getattr(record, "project_type", "") or "")
    columns = _canvas_identity_columns(getattr(record, "canvas", None), project_type)
    try:
        closure = upstream_plan.executable_closure(
            canvas=getattr(record, "canvas", None), project_type=project_type, group_id=str(record.group_id),
        )
    except Exception:  # i grafici di partenza non fermano la creazione della dashboard
        logger.warning("[dashboard] progetti a monte non leggibili: colonne dell'identita' solo del progetto",
                       exc_info=True)
        closure = {}
    for entry in closure.values():
        columns |= _canvas_identity_columns(entry.project.canvas, entry.project.project_type)
    return frozenset(columns)


# ── Grafana: visibilita' e sincronizzazione ─────────────────────────────────

def _admin_can_modify(record: Any) -> bool:
    """La STESSA regola dell'Eseguibile (``ownership.compute_can_edit``) per un
    admin qualunque del gruppo proprietario."""
    probe = CurrentUser(user_id="", username="", group_id=str(record.group_id), role="admin")
    return compute_can_edit(record, probe)


def folder_for(record: Any) -> str:
    """Una cartella per Eseguibile (i suoi permessi = quelli della dashboard)."""
    return dashboard_builder.folder_uid(str(record.id))


def folder_access(record: Any) -> grafana_sync.FolderAccess:
    """Chi vede la dashboard in Grafana = chi vede l'Eseguibile in Plenora
    (``auth.visibility``: gruppo proprietario + condivisioni, ``*`` = tutti;
    il superadmin e' Admin in Grafana). Chi la modifica = la regola
    dell'Eseguibile per un admin del gruppo proprietario."""
    owner = str(record.group_id)
    shares = {str(group) for group in list_resource_shares("eseguibili", str(record.id))}
    # B4: un gruppo cancellato (condivisione rimasta) non entra in Grafana.
    shares = {group for group in shares if group == MAGIC_ALL or users_db.get_group(group) is not None}
    return grafana_sync.FolderAccess(
        viewer_groups=frozenset({owner} | {group for group in shares if group != MAGIC_ALL}),
        everyone=MAGIC_ALL in shares,
        editor_group=owner if _admin_can_modify(record) else None,
    )


def _folder_title(record: Any) -> str:
    return f"{str(record.name)[:120]} ({record.id})"


def _plenora(row: Any) -> grafana_sync.PlenoraUser:
    return grafana_sync.PlenoraUser(
        user_id=str(row.id), username=str(row.username), role=str(row.role),
        group_id=str(row.group_id or ""), active=bool(row.is_active),
    )


def _users_by_group(group_ids: Iterable[str]) -> Mapping[str, List[grafana_sync.PlenoraUser]]:
    return {
        group_id: [_plenora(row) for row in users_db.list_users(group_id, limit=_GROUP_USERS_MAX)]
        for group_id in sorted(set(group_ids))
    }


def sync_eseguibile(
    record: Any,
    client: grafana_client.GrafanaApi,
    requester: Optional[CurrentUser] = None,
    *,
    users_by_group: Optional[dict[str, List[grafana_sync.PlenoraUser]]] = None,
) -> None:
    """Cartella dell'Eseguibile con i permessi della regola, pulizia dei team
    toccati e, se c'e', accesso di chi ha chiesto l'operazione (cosi' il link
    di modifica funziona subito). Idempotente. ``users_by_group``: utenti
    gia' letti (riallineamento di piu' Eseguibili: una lettura per gruppo)."""
    access = folder_access(record)
    grafana_sync.sync_folder(client, folder_uid=folder_for(record), title=_folder_title(record), access=access)
    groups = set(access.viewer_groups) | ({access.editor_group} if access.editor_group else set())
    if users_by_group is None:
        users = _users_by_group(groups)
    else:
        missing = sorted(group for group in groups if group not in users_by_group)
        users_by_group.update(_users_by_group(missing))
        users = users_by_group
    grafana_sync.prune_teams(client, access, users)
    if requester is not None:
        row = users_db.get_user(requester.user_id)
        if row is not None and row.is_active:
            grafana_access.forget(str(row.id))
            grafana_access.ensure_access(_plenora(row))


def on_shares_changed(eseguibile_id: str) -> None:
    """Condivisioni dell'Eseguibile cambiate (``auth.share_hooks``): la
    cartella in Grafana si riallinea subito. Grafana giu': la condivisione
    resta scritta, il log lo dice e la cartella si riallinea al prossimo
    riallineamento periodico (:func:`resync_all`, B3) o alla prossima
    creazione o «Modifica in Grafana»."""
    if not grafana_settings.enabled():
        return
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    if record is None or not getattr(record, "dashboard_uid", None):
        return
    sync_eseguibile(record, grafana_client.get_client())


share_hooks.register("eseguibili", on_shares_changed)


@dataclass(frozen=True)
class ResyncReport:
    folders: int = 0
    failed: int = 0
    teams_deleted: int = 0


def _delete_orphan_teams(client: grafana_client.GrafanaApi) -> int:
    """Team gestiti (``utenti-<g>``/``admin-<g>``) di gruppi che non esistono
    piu' in Plenora (cancellazione fatta con Grafana giu')."""
    existing = {str(group.id) for group in users_db.list_groups(limit=10_000)}
    if not existing:
        return 0  # elenco dei gruppi non letto (c'e' sempre «default»): non si cancella nulla
    deleted = 0
    for name, team_id in sorted(client.managed_teams().items()):
        group_id = name.split("-", 1)[1] if "-" in name else ""
        if group_id and group_id not in existing:
            client.delete_team(int(team_id))
            deleted += 1
    return deleted


def resync_all() -> ResyncReport:
    """Riallineamento periodico (beat, B3): cartella di OGNI Eseguibile con
    dashboard e team di gruppi cancellati. Solo con Grafana attivo; un
    Eseguibile che non si riallinea non ferma gli altri (si riprova al giro
    dopo). Idempotente."""
    if not grafana_settings.enabled():
        return ResyncReport()
    client = grafana_client.get_client()
    users: dict[str, List[grafana_sync.PlenoraUser]] = {}
    folders = failed = 0
    for record in eseguibili_db.list_dashboard_eseguibili():
        try:
            sync_eseguibile(record, client, users_by_group=users)
            folders += 1
        except grafana_client.GrafanaUnavailable:
            raise  # Grafana giu': si riprova al prossimo giro
        except Exception:  # un Eseguibile non ferma il riallineamento degli altri
            failed += 1
            logger.warning(f"[dashboard] riallineamento della cartella di {record.id} non riuscito", exc_info=True)
    return ResyncReport(folders=folders, failed=failed, teams_deleted=_delete_orphan_teams(client))


def on_group_deleted(group_id: str) -> None:
    """Gruppo cancellato (``auth.share_hooks.notify_group_deleted``, B4):
    elimina i suoi team in Grafana e riallinea le cartelle degli Eseguibili
    condivisi con lui. Best effort, MAI bloccare la cancellazione: con
    Grafana giu' lo completa il riallineamento periodico (:func:`resync_all`)."""
    if not grafana_settings.enabled():
        return
    try:
        client = grafana_client.get_client()
        for name in (grafana_sync.users_team(group_id), grafana_sync.admins_team(group_id)):
            team_id = client.find_team(name)
            if team_id is not None:
                client.delete_team(int(team_id))
        for eseguibile_id in eseguibili_db.list_eseguibili_shared_with(group_id):
            record = eseguibili_db.get_eseguibile(eseguibile_id)
            if record is not None and getattr(record, "dashboard_uid", None):
                sync_eseguibile(record, client)
    except Exception:  # best effort dichiarato: mai bloccare la cancellazione del gruppo
        logger.warning(
            f"[dashboard] pulizia in Grafana del gruppo {group_id} non riuscita: la completa il riallineamento "
            "periodico", exc_info=True,
        )


share_hooks.register_group_deleted(on_group_deleted)


# ── Avvisi: lettura della dashboard con timeout breve e cache ──────────────

_WARN_OK = "ok"
_WARN_MISSING = "missing"
_WARN_UNAVAILABLE = "unavailable"


class _DashboardReadCache:
    """Dashboard lette da Grafana per gli avvisi del dettaglio: timeout
    dedicato (``GRAFANA_WARNINGS_TIMEOUT_SECONDS``) e risultato in cache
    (``GRAFANA_WARNINGS_CACHE_SECONDS``), anche quando Grafana non risponde:
    il dettaglio di un Eseguibile non aspetta mai piu' del timeout breve, e
    al piu' una volta per intervallo."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, str, Optional[Mapping[str, Any]]]] = {}

    def get(self, uid: str, *, now: Optional[float] = None) -> tuple[str, Optional[Mapping[str, Any]]]:
        current = time.monotonic() if now is None else now
        with self._lock:
            entry = self._entries.get(uid)
        if entry is not None and entry[0] > current:
            return entry[1], entry[2]
        try:
            found = grafana_client.get_client(timeout=grafana_settings.warnings_timeout_seconds()).get_dashboard(uid)
            status, dashboard = (_WARN_MISSING, None) if found is None else (_WARN_OK, found["dashboard"])
        except (grafana_client.GrafanaUnavailable, grafana_client.GrafanaError, grafana_client.GrafanaDisabled):
            status, dashboard = _WARN_UNAVAILABLE, None
        self.put(uid, status, dashboard, now=current)
        return status, dashboard

    def put(self, uid: str, status: str, dashboard: Optional[Mapping[str, Any]], *, now: Optional[float] = None) -> None:
        current = time.monotonic() if now is None else now
        with self._lock:
            if len(self._entries) > 10_000:
                self._entries = {k: v for k, v in self._entries.items() if v[0] > current}
            self._entries[uid] = (current + grafana_settings.warnings_cache_seconds(), status, dashboard)

    def forget(self, uid: Optional[str] = None) -> None:
        with self._lock:
            if uid is None:
                self._entries.clear()
            else:
                self._entries.pop(uid, None)


dashboard_read_cache = _DashboardReadCache()


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
        excluded_chart_columns=identity_columns(record),
    )
    sync_eseguibile(record, client, requester)
    client.upsert_dashboard(dashboard, folder_uid=folder_for(record), message="generata da Plenora")
    dashboard_read_cache.put(uid, _WARN_OK, dashboard)
    updated = eseguibili_db.set_dashboard_uid(record.id, uid)
    return updated if updated is not None else record


def _delete_in_grafana(client: grafana_client.GrafanaApi, record: Any) -> None:
    client.delete_dashboard(str(record.dashboard_uid))
    client.delete_folder(folder_for(record))
    dashboard_read_cache.forget(str(record.dashboard_uid))


def delete_dashboard(record: Any) -> Any:
    """Elimina dashboard e cartella (Grafana 404 = gia' eliminate) e la slega."""
    assert_enabled()
    if not record.dashboard_uid:
        raise DashboardMissing("L'Eseguibile non ha una dashboard")
    _delete_in_grafana(grafana_client.get_client(), record)
    updated = eseguibili_db.set_dashboard_uid(record.id, None)
    return updated if updated is not None else record


def delete_dashboard_best_effort(record: Any) -> None:
    """Eliminazione dell'Eseguibile: mai bloccarla se Grafana non risponde."""
    if record is None or not getattr(record, "dashboard_uid", None) or not grafana_settings.enabled():
        return
    try:
        _delete_in_grafana(grafana_client.get_client(), record)
    except Exception:
        logger.warning(
            f"[dashboard] eliminazione della dashboard {record.dashboard_uid} in Grafana non riuscita: resta in Grafana",
            exc_info=True,
        )


def edit_link(record: Any, requester: CurrentUser, *, task_id: Optional[str] = None) -> Mapping[str, object]:
    """«Modifica in Grafana»: sincronizza (cartella, team, utente) e
    restituisce il link; con ``task_id`` anche i dati di quell'esecuzione."""
    assert_enabled()
    if not record.dashboard_uid:
        raise DashboardMissing("L'Eseguibile non ha una dashboard")
    uid = str(record.dashboard_uid)
    client = grafana_client.get_client()
    sync_eseguibile(record, client, requester)
    entry = client.get_dashboard(uid)
    if entry is None:
        dashboard_read_cache.put(uid, _WARN_MISSING, None)
        raise DashboardMissing("La dashboard non esiste piu' in Grafana: eliminala e ricreala")
    current = entry["dashboard"]
    dashboard_read_cache.put(uid, _WARN_OK, current)
    # La cartella segue la regola (anche per dashboard nate in una cartella
    # di gruppo della versione precedente).
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
    """Colonne usate dalla dashboard che non sono tra le colonne attese.
    Senza ``dashboard`` la legge da Grafana con timeout breve e cache
    (:data:`dashboard_read_cache`)."""
    if not getattr(record, "dashboard_uid", None) or not grafana_settings.enabled():
        return []
    if dashboard is None:
        status, dashboard = dashboard_read_cache.get(str(record.dashboard_uid))
        if status == _WARN_UNAVAILABLE:
            return [{"column": None, "motivo": "Dashboard non verificabile: Grafana non risponde"}]
        if status == _WARN_MISSING or dashboard is None:
            return [{"column": None, "motivo": "La dashboard non esiste piu' in Grafana: eliminala e ricreala"}]
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


@dataclass(frozen=True)
class ReadGrant:
    """Lettura dei dati autorizzata dal lasciapassare (prima di contarla)."""

    task_id: str
    group_id: str
    user_id: str


def authorize_read(task_id: str, token: Optional[str], *, now: Optional[float] = None) -> ReadGrant:
    """Endpoint dati: la decisione d'accesso e' SOLO il lasciapassare (firma,
    tipo, scadenza, task del path, utente attivo, esecuzione sua)."""
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
    return ReadGrant(task_id=task_id, group_id=str(record.group_id), user_id=str(user.id))


def read_granted(grant: ReadGrant, *, now: Optional[float] = None) -> bytes:
    return dashboard_data.read(grant.task_id, group_id=grant.group_id, now=now)


def read_rows(task_id: str, token: Optional[str], *, now: Optional[float] = None) -> bytes:
    """Autorizza e legge (l'endpoint conta le letture tra i due passi)."""
    return read_granted(authorize_read(task_id, token, now=now), now=now)


__all__ = [
    "DashboardDisabled",
    "DashboardExists",
    "DashboardMissing",
    "PassRejected",
    "ReadGrant",
    "TaskNotOwned",
    "assert_enabled",
    "authorize_read",
    "create_dashboard",
    "dashboard_read_cache",
    "dashboard_warnings",
    "delete_dashboard",
    "delete_dashboard_best_effort",
    "edit_link",
    "expected_output_columns",
    "folder_access",
    "folder_for",
    "iframe_path",
    "is_safe_task_id",
    "issue_pass",
    "on_group_deleted",
    "on_shares_changed",
    "identity_columns",
    "resync_all",
    "ResyncReport",
    "KIOSK_PARAMS",
    "read_granted",
    "read_rows",
    "sync_eseguibile",
    "view_path",
]
