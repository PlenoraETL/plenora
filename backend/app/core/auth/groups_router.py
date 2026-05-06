"""Auth groups admin CRUD router — typed boundary.

BE-GO-READY-AUTH-ENGINE-004-GROUPS-ROUTER-EXTRACTION.

Estrae da `auth.py` i 4 endpoint admin CRUD `/auth/groups*`:

- `GET    /auth/groups`
- `POST   /auth/groups`
- `GET    /auth/groups/{group_id}/summary`
- `DELETE /auth/groups/{group_id}`

Boundary contract
=================
Il router NON conosce:

- token decode / disclosure-on-token (`get_current_user`,
  `decode_token`, `is_jti_blacklisted` — vivono in `auth.py`);
- password hashing low-level (`app.shared.password_hashing`);
- rate-limit fallback (`app.core.auth.rate_limit_fallback`);
- MFA enroll/verify/disable (`app.core.auth.mfa`);
- ownership / require_*_owner (`app.core.auth.ownership`);
- users CRUD (`app.core.auth.users_router`).

Behavior preserved 1:1
======================
- Stessi error code / messaggi italiani / status code / headers /
  anti-enumeration invariants definiti in
  `app.core.auth.disclosure`. Nessun cambio user-facing.
- Stessi audit log via `audit_action` decorator.
- Stessa dipendenza di accesso:
  - `require_admin` per `list_groups`;
  - `require_superadmin` per `create_group`, `get_group_summary`,
    `delete_group`.
- Stessa cascade-delete behavior in `delete_group` con `default`
  group block + `INTERNAL_ERROR` 500 fallback su exception nel
  cascade.

KEEP_COMPAT
===========
`auth.py` ri-esporta `groups_router` (vedi `auth.py` post-batch),
per cui `from app.core.auth.auth import groups_router` continua a
funzionare. `main.py` lo importa direttamente dal nuovo modulo per
montarlo (`app.include_router(groups_router, prefix="/api")`).

Circular-import avoidance
=========================
Il router NON fa `from app.core.auth.auth import require_admin /
require_superadmin / get_current_user` al top-level: quel pattern
crea un cycle `groups_router -> auth.py -> groups_router` quando
`auth.py` ri-esporta a sua volta i nomi del modulo nuovo. Mirror
del fix gia' applicato a `users_router.py`: HTTPBearer +
proxy-locali con import lazy.

Go-ready mapping
================
    type GroupsRouter struct { ... }
    func (r *GroupsRouter) ListGroups(ctx, currentUser) ([]GroupResponse, error)
    func (r *GroupsRouter) CreateGroup(ctx, payload, currentUser) (GroupResponse, error)
    ...
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth import disclosure as _disclosure
from app.core.auth.models import (
    CurrentUser,
    GroupCreate,
    GroupResponse,
)
from app.shared import error_codes
from app.shared.audit_action import audit_action
from app.shared.error_envelope import raise_typed_http
from app.utils.logger import get_logger

# BE-GO-READY-DB-ENGINE-003-GROUPS-CALLSITE-MIGRATION: import the
# canonical leaf module directly; the previous `app.db.db_service`
# / `app.db.config_db_service` indirections were strangler facades
# kept only while callsites migrated. Module-as-attribute so tests
# can `monkeypatch.setattr(groups_mod._groups, "get_group", ...)`.
from app.db.config import groups as _groups


logger = get_logger(__name__)


__all__ = ["groups_router"]


groups_router = APIRouter(prefix="/auth/groups", tags=["groups"])
_security = HTTPBearer(auto_error=False)


def _get_current_user_proxy(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> CurrentUser:
    """Lazy proxy to avoid `groups_router -> auth.py -> groups_router` cycle."""
    from app.core.auth.auth import get_current_user

    return get_current_user(credentials)


def require_admin(
    current_user: CurrentUser = Depends(_get_current_user_proxy),
) -> CurrentUser:
    """Lazy proxy preserving the existing `auth.require_admin` behavior."""
    from app.core.auth.auth import require_admin as _require_admin

    return _require_admin(current_user)


def require_superadmin(
    current_user: CurrentUser = Depends(_get_current_user_proxy),
) -> CurrentUser:
    """Lazy proxy preserving the existing `auth.require_superadmin` behavior."""
    from app.core.auth.auth import require_superadmin as _require_superadmin

    return _require_superadmin(current_user)


@groups_router.get("", response_model=list[GroupResponse])
def list_groups(current_user: CurrentUser = Depends(require_admin)):
    """
    Lista gruppi.
    superadmin → tutti i gruppi (cross-tenant)
    admin      → solo il proprio gruppo (l'admin non può fare sharing
                  cross-gruppo, quindi non ha bisogno di vedere gli altri)
    """
    if current_user.role == "superadmin":
        groups = _groups.list_groups()
    else:
        g = _groups.get_group(current_user.group_id)
        groups = [g] if g else []
    # Bug #3 (2026-04-23): description=NULL nel DB ritorna None da dict.get,
    # non la stringa default; GroupResponse.description: str alza ValidationError.
    # Stesso pattern null-safe applicato su name per robustezza.
    return [GroupResponse(
                group_id=g["id"],
                name=g.get("name") or "",
                description=g.get("description") or "")
            for g in groups if g]


@groups_router.post("", response_model=GroupResponse, status_code=201)
@audit_action(action="group.create")
def create_group(data: GroupCreate,
                 current_user: CurrentUser = Depends(require_superadmin)):
    """Crea nuovo gruppo (solo superadmin)."""
    if _groups.get_group(data.group_id):
        _disclosure.raise_disclosure(_disclosure.group_id_already_exists())
    group = _groups.create_group(data.group_id, data.name, data.description)
    return GroupResponse(group_id=group["id"], name=group["name"], description=group.get("description", ""))


@groups_router.get("/{group_id}/summary")
def get_group_summary(group_id: str,
                      current_user: CurrentUser = Depends(require_superadmin)):
    """
    Ritorna il conteggio di tutte le entità del gruppo.
    Usato dal frontend per mostrare il modale di conferma prima del cascade delete.
    """
    if not _groups.get_group(group_id):
        _disclosure.raise_disclosure(_disclosure.group_not_found())
    return _groups.get_group_summary(group_id)


@groups_router.delete("/{group_id}", status_code=200)
@audit_action(action="group.delete", target_param="group_id")
def delete_group(group_id: str,
                 current_user: CurrentUser = Depends(require_superadmin)):
    """
    Cascade delete gruppo (solo superadmin).
    Elimina in ordine: schedules, api_connections, datasources, file_sources,
    connections, projects (+ results.db + intermediate.db), users, log.db, gruppo.
    Blocca solo se il gruppo è 'default'.
    """
    if group_id == "default":
        _disclosure.raise_disclosure(_disclosure.group_default_not_deletable())
    if not _groups.get_group(group_id):
        _disclosure.raise_disclosure(_disclosure.group_not_found())

    try:
        report = _groups.delete_group_cascade(group_id)
    except Exception as e:
        logger.error(f"[CRASH] delete_group_cascade fallito per group_id={group_id}: {e}", exc_info=True)
        raise_typed_http(
            error_codes.INTERNAL_ERROR,
            status_code=500,
            message="Errore interno del server",
        )

    return {"deleted": True, "group_id": group_id, "report": report}
