"""
Auth groups admin CRUD router — typed boundary.

NOTE: Manteniamo l'assenza di `from __future__ import annotations` per
il contratto storico di binding dei body FastAPI/Pydantic. Su Python 3.14
le annotazioni sono differite anche senza PEP 563: import dei router,
OpenAPI e binding body/query devono essere verificati con lo stack risolto.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from typing_extensions import TypedDict

from app.core.auth import disclosure as _disclosure
from app.core.auth.models import (
    GroupCreate,
    GroupResponse,
)
from app.shared.contracts.auth import CurrentUser
from app.shared import error_codes
from app.observability.audit_action import audit_action
from app.shared.error_envelope import raise_typed_http
from app.utils.logger import get_logger

from app.core.auth import db as _groups
from app.core.auth import group_cleanup as _group_cleanup
from app.core.auth import share_hooks


logger = get_logger(__name__)


__all__ = ["groups_router"]


groups_router = APIRouter(prefix="/auth/groups", tags=["groups"])
_security = HTTPBearer(auto_error=False)


class GroupResponsePayload(TypedDict):
    group_id: str
    name: str
    description: str


class GroupSummaryPayload(TypedDict):
    projects: int
    users: int
    connections: int
    file_sources: int
    datasources: int
    api_connections: int
    schedules: int


def _group_response_payload(group: _groups.GroupRow) -> GroupResponsePayload:
    return {
        "group_id": group.id,
        "name": group.name or "",
        "description": group.description or "",
    }


def _group_summary_payload(summary: _groups.GroupSummaryRow) -> GroupSummaryPayload:
    return {
        "projects": summary.projects,
        "users": summary.users,
        "connections": summary.connections,
        "file_sources": summary.file_sources,
        "datasources": summary.datasources,
        "api_connections": summary.api_connections,
        "schedules": summary.schedules,
    }


def _get_current_user_proxy(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> CurrentUser:
    from app.core.auth.dependencies import get_current_user

    return get_current_user(credentials)


def require_admin(current_user: CurrentUser = Depends(_get_current_user_proxy)) -> CurrentUser:
    from app.core.auth.dependencies import require_admin as _require_admin

    return _require_admin(current_user)


def require_superadmin(
    current_user: CurrentUser = Depends(_get_current_user_proxy),
) -> CurrentUser:
    from app.core.auth.dependencies import require_superadmin as _require_superadmin

    return _require_superadmin(current_user)


@groups_router.get("", response_model=list[GroupResponse])
def list_groups(
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: CurrentUser = Depends(require_admin),
):
    """
    Lista gruppi.
    superadmin → tutti i gruppi (cross-tenant)
    admin → solo il proprio gruppo (l'admin non può fare sharing
                  cross-gruppo, quindi non ha bisogno di vedere gli altri)
    """
    if current_user.role == "superadmin":
        groups = _groups.list_groups(limit=limit, offset=offset)
    else:
        g = _groups.get_group(current_user.group_id)
        groups = [g] if g and offset == 0 else []
    return [GroupResponse(**_group_response_payload(group)) for group in groups if group]


@groups_router.post("", response_model=GroupResponse, status_code=201)
@audit_action(action="group.create")
def create_group(data: GroupCreate,
                 current_user: CurrentUser = Depends(require_superadmin)):
    """Crea nuovo gruppo (solo superadmin)."""
    if _groups.get_group(data.group_id):
        _disclosure.raise_disclosure(_disclosure.group_id_already_exists())
    group = _groups.create_group(data.group_id, data.name, data.description)
    return GroupResponse(**_group_response_payload(group))


@groups_router.get("/{group_id}/summary")
def get_group_summary(group_id: str,
                      current_user: CurrentUser = Depends(require_superadmin)):
    """
    Ritorna il conteggio di tutte le entità del gruppo.
    Usato dal frontend per mostrare il modale di conferma prima del cascade delete.
    """
    if not _groups.get_group(group_id):
        _disclosure.raise_disclosure(_disclosure.group_not_found())
    return _group_summary_payload(_groups.get_group_summary(group_id))


@groups_router.delete("/{group_id}", status_code=200)
@audit_action(action="group.delete", target_param="group_id")
def delete_group(group_id: str,
                 current_user: CurrentUser = Depends(require_superadmin)):
    """
    Cascade delete gruppo (solo superadmin).
    Elimina in ordine: schedules, api_connections, datasources, file_sources,
    connections, projects (+ results/intermediate cleanup), users, log.db, gruppo.
    Blocca solo se il gruppo è 'default'.
    """
    if group_id == "default":
        _disclosure.raise_disclosure(_disclosure.group_default_not_deletable())
    if not _groups.get_group(group_id):
        _disclosure.raise_disclosure(_disclosure.group_not_found())

    try:
        report = _group_cleanup.delete_group_cascade(group_id)
    except Exception as e:
        logger.error(f"[CRASH] delete_group_cascade fallito per group_id={group_id}: {e}", exc_info=True)
        raise_typed_http(
            error_codes.INTERNAL_ERROR,
            status_code=500,
            message="Errore interno del server",
        )

    # Copie fuori dal DB (team e cartelle Grafana): best effort, mai bloccare.
    share_hooks.notify_group_deleted(group_id)
    return {"deleted": True, "group_id": group_id, "report": report}
