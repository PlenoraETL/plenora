"""GET /dashboard/counts — i numeri della dashboard (UI-1, 06/11/2026, audit C4).

La dashboard scaricava tre liste intere (connessioni API, data source,
schedule) solo per contarle o per contare gli schedule di ogni progetto. Qui
il server le conta e risponde con i numeri.

Regola di visibilita': i conteggi passano dalle STESSE funzioni delle liste
(`GET /api-connections`, `GET /datasources`, `GET /schedules`), a pagine,
quindi contano esattamente cio' che la lista mostrerebbe all'utente. Non si
allega l'esito dei test di connessione (`__wrapped__`: il decoratore della
lista serve solo a chi mostra le righe).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.arricchisci import router as api_connections_routes
from app.core.auth.dependencies import require_not_esecutore
from app.core.datasources import router as datasources_routes
from app.core.scheduler import router as schedules_routes
from app.shared.contracts.auth import CurrentUser

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Pagina interna dei conteggi: il massimo ammesso dalle liste.
_PAGE = 500
_MAX_PAGES = 1000


class DashboardCounts(BaseModel):
    """Numeri della dashboard visibili all'utente."""

    api_connections: int = 0
    datasources: int = 0
    schedules_active: int = 0
    schedules_by_project: dict[str, int] = {}


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _all_items(list_page: Callable[[int, int], Any], items_field: str) -> list[Any]:
    """Tutte le righe di una lista paginata (limit/offset), come il client."""
    out: list[Any] = []
    for page in range(_MAX_PAGES):
        items = _field(list_page(_PAGE, page * _PAGE), items_field) or []
        out.extend(items)
        if len(items) < _PAGE:
            break
    return out


def _unwrapped(func: Callable) -> Callable:
    return getattr(func, "__wrapped__", func)


@router.get("/counts", response_model=DashboardCounts)
def get_dashboard_counts(
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> DashboardCounts:
    """Connessioni API, data source, schedule attivi e schedule per progetto."""
    # Le route delle liste sono la fonte unica della visibilita' (lette al
    # momento della richiesta: i test le sostituiscono sul modulo).
    list_api = _unwrapped(api_connections_routes.list_api_connections)
    list_ds = _unwrapped(datasources_routes.list_datasources)
    list_schedules = _unwrapped(schedules_routes.list_schedules)

    api_connections = _all_items(
        lambda limit, offset: list_api(
            category=None, connection_type=None, limit=limit, offset=offset,
            current_user=current_user,
        ),
        "api_connections",
    )
    datasources = _all_items(
        lambda limit, offset: list_ds(limit=limit, offset=offset, current_user=current_user),
        "datasources",
    )
    schedules = _all_items(
        lambda limit, offset: list_schedules(
            project_id=None, limit=limit, offset=offset, current_user=current_user,
        ),
        "schedules",
    )

    by_project: dict[str, int] = {}
    active = 0
    for item in schedules:
        if _field(_field(item, "schedule"), "enabled"):
            active += 1
        project_id = _field(_field(item, "pipeline"), "project_id")
        if project_id:
            key = str(project_id)
            by_project[key] = by_project.get(key, 0) + 1

    return DashboardCounts(
        api_connections=len(api_connections),
        datasources=len(datasources),
        schedules_active=active,
        schedules_by_project=by_project,
    )
