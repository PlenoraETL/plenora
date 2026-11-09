"""GET /dashboard/graph — nodi e collegamenti della home per catene (09/11/2026).

La home mostra i progetti come catene (Connessione -> Estrai -> Manipola ->
Pubblicato). Le servono, per ogni progetto, da dove legge e dove scrive,
l'ultima esecuzione e le pianificazioni; per ogni Eseguibile il progetto
sorgente e gli input. Nessun canvas viaggia: i collegamenti li estrae il DB
(`projects.usage.list_project_graph_refs`, stesse regole della query delle
sorgenti Manipola di GET /projects).

Regole (come GET /dashboard/counts):
  - visibilita': le STESSE funzioni delle liste — progetti come GET /projects
    (`group_filter`), Eseguibili dalla route GET /eseguibili, pianificazioni
    dalla route GET /schedules, esecuzioni come
    GET /pipeline-runs/latest-by-project (`latest_runs_by_project`);
  - una parte per volta, indipendenti: una che fallisce e' elencata in
    `unavailable` ("links", "runs", "eseguibili", "schedules") e le altre
    restano; senza progetti la risposta e' un errore (la home non ha senso).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.core.eseguibili import router as eseguibili_routes
from app.core.logs import runs_router as runs_routes
from app.core.projects import router as projects_routes
from app.core.projects import usage as projects_usage
from app.core.scheduler import router as schedules_routes
from app.shared.contracts.auth import CurrentUser, group_filter
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Le parti che possono mancare (in `unavailable`).
GRAPH_PARTS = ("links", "runs", "eseguibili", "schedules")


class GraphRun(BaseModel):
    status: Optional[str] = None
    at: Optional[str] = None


class GraphConnectionRef(BaseModel):
    id: str
    # "db" (nodi tabella, connessione del progetto) o "arcgis" (layer).
    kind: str = "db"


class GraphDatasourceRef(BaseModel):
    id: str
    name: str = ""


class GraphProject(BaseModel):
    id: str
    name: str
    project_type: str = "estrai"
    default_connection_id: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    connections: list[GraphConnectionRef] = Field(default_factory=list)
    source_project_ids: list[str] = Field(default_factory=list)
    file_source_ids: list[str] = Field(default_factory=list)
    datasources: list[GraphDatasourceRef] = Field(default_factory=list)
    # Destinazioni di Carica: "db", "rest", "arcgis", "datasource".
    loads: list[str] = Field(default_factory=list)
    # Tabelle lette o scritte (per la ricerca della home).
    tables: list[str] = Field(default_factory=list)
    canvas_corrupted: bool = False
    # Ultima esecuzione (qualunque esito) e ultima riuscita.
    last_run: Optional[GraphRun] = None
    last_success_at: Optional[str] = None
    schedules: int = 0
    schedules_active: int = 0
    updated_at: Optional[str] = None


class GraphEseguibile(BaseModel):
    id: str
    name: str
    project_type: str = "arricchisci"
    source_project_id: Optional[str] = None
    inputs_count: int = 0
    has_manifest: bool = False
    active_version: int = 1
    updated_at: Optional[str] = None


class DashboardGraph(BaseModel):
    projects: list[GraphProject] = Field(default_factory=list)
    eseguibili: list[GraphEseguibile] = Field(default_factory=list)
    unavailable: list[str] = Field(default_factory=list)


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _iso(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _unwrapped(func: Callable) -> Callable:
    return getattr(func, "__wrapped__", func)


def build_dashboard_graph(
    current_user: CurrentUser,
    *,
    all_items: Callable[[Callable[[int, int], Any], str], list[Any]],
) -> DashboardGraph:
    """Il grafo visibile all'utente; `all_items` legge una lista paginata."""
    graph = DashboardGraph()

    def part(name: str, compute: Callable[[], None]) -> None:
        try:
            compute()
        except Exception as exc:  # una parte non toglie le altre
            logger.warning(
                f"[GESTITO] dashboard/graph: parte {name} non riuscita: "
                f"{type(exc).__name__}: {exc}",
                op="dashboard_graph",
            )
            graph.unavailable.append(name)

    # Progetti: la stessa lettura di GET /projects (gruppo dell'utente; il
    # superadmin li vede tutti). Un errore qui e' un errore della risposta.
    service = projects_routes._project_service
    gid = group_filter(current_user)
    metas = all_items(
        lambda limit, offset: service.list_projects(group_id=gid, limit=limit, offset=offset),
        "projects",
    )
    projects: dict[str, GraphProject] = {}
    for meta in metas:
        pid = str(_field(meta, "id"))
        projects[pid] = GraphProject(
            id=pid,
            name=str(_field(meta, "name") or ""),
            project_type=str(_field(meta, "project_type") or "estrai"),
            default_connection_id=_field(meta, "default_connection_id") or None,
            tags=list(_field(meta, "tags") or []),
            updated_at=_iso(_field(meta, "updated_at")),
        )

    def links() -> None:
        for refs in projects_usage.list_project_graph_refs(set(projects)):
            p = projects.get(refs.project_id)
            if p is None:
                continue
            p.source_project_ids = list(refs.source_project_ids)
            p.file_source_ids = list(refs.file_source_ids)
            p.connections = [GraphConnectionRef(id=cid, kind=kind) for cid, kind in refs.connections]
            p.datasources = [GraphDatasourceRef(id=did, name=name) for did, name in refs.datasources]
            p.loads = list(refs.loads)
            p.tables = list(refs.tables)
            p.canvas_corrupted = refs.corrupted

    def runs() -> None:
        latest = runs_routes.latest_runs_by_project(current_user)
        succeeded = runs_routes.latest_runs_by_project(current_user, status="success")
        for pid, p in projects.items():
            run = latest.get(pid)
            if run:
                p.last_run = GraphRun(status=run.get("status"), at=_iso(run.get("ts_start")))
            ok = succeeded.get(pid)
            p.last_success_at = _iso(ok.get("ts_start")) if ok else None

    def eseguibili() -> None:
        # La route stessa: visibilita' del gruppo e condivisioni (allowed_groups).
        list_route = _unwrapped(eseguibili_routes.list_eseguibili)
        items = all_items(
            lambda limit, offset: list_route(limit=limit, offset=offset, current_user=current_user),
            "eseguibili",
        )
        graph.eseguibili = [
            GraphEseguibile(
                id=str(_field(item, "id")),
                name=str(_field(item, "name") or ""),
                project_type=str(_field(item, "project_type") or "arricchisci"),
                source_project_id=_field(item, "source_canvas_id") or None,
                inputs_count=int(_field(item, "inputs_count") or 0),
                has_manifest=bool(_field(item, "has_manifest")),
                active_version=int(_field(item, "active_version") or 1),
                updated_at=_iso(_field(item, "updated_at")),
            )
            for item in items
        ]

    def schedules() -> None:
        list_route = _unwrapped(schedules_routes.list_schedules)
        items = all_items(
            lambda limit, offset: list_route(
                project_id=None, limit=limit, offset=offset, current_user=current_user,
            ),
            "schedules",
        )
        for item in items:
            p = projects.get(str(_field(_field(item, "pipeline"), "project_id") or ""))
            if p is None:
                continue
            p.schedules += 1
            if _field(_field(item, "schedule"), "enabled"):
                p.schedules_active += 1

    part("links", links)
    part("runs", runs)
    part("eseguibili", eseguibili)
    part("schedules", schedules)
    graph.projects = list(projects.values())
    return graph
