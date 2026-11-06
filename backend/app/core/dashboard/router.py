"""GET /dashboard/counts — i numeri della dashboard (UI-1, 06/11/2026, audit C4).

La dashboard scaricava tre liste intere (connessioni API, data source,
schedule) solo per contarle o per contare gli schedule di ogni progetto. Qui
il server le conta e risponde con i numeri.

Regole (conferma UI C-K10):
  - visibilita': i conteggi passano dalle STESSE funzioni di visibilita'
    delle liste (`visible_api_connections`, `visible_datasources`, la route
    `GET /schedules`), a pagine, quindi contano esattamente cio' che la lista
    mostrerebbe; ma SENZA il lavoro per riga che serve solo a chi mostra le
    righe (`can_edit`, esito dei test di connessione);
  - un conteggio per volta, indipendenti: uno che fallisce vale `null` ed
    e' elencato in `unavailable`, gli altri restano (mai tutto o niente).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.arricchisci import router as api_connections_routes
from app.core.auth.dependencies import require_not_esecutore
from app.core.datasources import router as datasources_routes
from app.core.scheduler import router as schedules_routes
from app.shared.contracts.auth import CurrentUser
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Pagina interna dei conteggi: il massimo ammesso dalle liste.
_PAGE = 500
_MAX_PAGES = 1000


class DashboardCounts(BaseModel):
    """Numeri della dashboard visibili all'utente (`null` = non disponibile)."""

    api_connections: Optional[int] = 0
    datasources: Optional[int] = 0
    schedules_active: Optional[int] = 0
    schedules_by_project: Optional[dict[str, int]] = Field(default_factory=dict)
    # Conteggi non calcolati: "api_connections", "datasources", "schedules".
    unavailable: list[str] = Field(default_factory=list)


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _items(page: Any, items_field: str) -> list[Any]:
    if isinstance(page, list):
        return page
    return _field(page, items_field) or []


def _all_items(list_page: Callable[[int, int], Any], items_field: str) -> list[Any]:
    """Tutte le righe di una lista paginata (limit/offset), come il client."""
    out: list[Any] = []
    for page in range(_MAX_PAGES):
        items = _items(list_page(_PAGE, page * _PAGE), items_field)
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
    # Lette al momento della richiesta: i test le sostituiscono sul modulo.
    visible_api = api_connections_routes.visible_api_connections
    visible_ds = datasources_routes.visible_datasources
    list_schedules = _unwrapped(schedules_routes.list_schedules)

    counts = DashboardCounts()

    def count(name: str, compute: Callable[[], None]) -> None:
        try:
            compute()
        except Exception as exc:  # un conteggio non toglie gli altri
            logger.warning(
                f"[GESTITO] dashboard/counts: conteggio {name} non riuscito: "
                f"{type(exc).__name__}: {exc}",
                op="dashboard_counts",
            )
            counts.unavailable.append(name)

    def api() -> None:
        counts.api_connections = None
        counts.api_connections = len(_all_items(
            lambda limit, offset: visible_api(current_user, limit=limit, offset=offset),
            "api_connections",
        ))

    def datasources() -> None:
        counts.datasources = None
        counts.datasources = len(_all_items(
            lambda limit, offset: visible_ds(current_user, limit=limit, offset=offset),
            "datasources",
        ))

    def schedules() -> None:
        counts.schedules_active = None
        counts.schedules_by_project = None
        items = _all_items(
            lambda limit, offset: list_schedules(
                project_id=None, limit=limit, offset=offset, current_user=current_user,
            ),
            "schedules",
        )
        by_project: dict[str, int] = {}
        active = 0
        for item in items:
            if _field(_field(item, "schedule"), "enabled"):
                active += 1
            project_id = _field(_field(item, "pipeline"), "project_id")
            if project_id:
                key = str(project_id)
                by_project[key] = by_project.get(key, 0) + 1
        counts.schedules_active = active
        counts.schedules_by_project = by_project

    count("api_connections", api)
    count("datasources", datasources)
    count("schedules", schedules)
    return counts
