"""
Auth users admin CRUD router — typed boundary.

"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth import disclosure as _disclosure
from app.core.auth.models import (
    ResetPasswordRequest,
    UserCreate,
    UserResponse,
)
from app.shared.contracts.auth import CurrentUser
from app.core.auth.roles import ROLES_ADMIN_OR_ABOVE, VALID_ROLES
from app.observability.audit_action import audit_action
from app.shared.password_hashing import hash_password

from app.core.auth import db as _groups_dal
from app.core.auth import db as _users_dal


__all__ = ["users_router"]


users_router = APIRouter(prefix="/auth/users", tags=["users"])
_security = HTTPBearer(auto_error=False)


def _user_response_payload(user: _users_dal.UserRow) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "group_id": user.group_id,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at,
    }


def _get_current_user_proxy(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> CurrentUser:
    """Lazy proxy to avoid `users_router -> auth.py -> users_router` cycle.
     import moved to `dependencies` leaf.
    """
    from app.core.auth.dependencies import get_current_user

    return get_current_user(credentials)


def require_admin(
    current_user: CurrentUser = Depends(_get_current_user_proxy),
) -> CurrentUser:
    """Lazy proxy preserving the existing `auth.require_admin` behavior."""
    from app.core.auth.dependencies import require_admin as _require_admin

    return _require_admin(current_user)


@users_router.get("", response_model=list[UserResponse])
def list_users(current_user: CurrentUser = Depends(require_admin)):
    """
    Lista utenti.
    superadmin → tutti gli utenti di tutti i gruppi
    admin → solo utenti del proprio gruppo
    """
    if current_user.role == "superadmin":
        users = _users_dal.list_all_users()
    else:
        users = _users_dal.list_users(current_user.group_id)
    return [UserResponse(**_user_response_payload(user)) for user in users]


@users_router.post("", response_model=UserResponse, status_code=201)
@audit_action(action="user.create")
def create_user(data: UserCreate, current_user: CurrentUser = Depends(require_admin)):
    """
    Crea nuovo utente.
    superadmin → qualsiasi gruppo, qualsiasi ruolo (viewer/user/admin/superadmin)
    admin → solo nel proprio gruppo, solo ruolo viewer o user
    """
    if data.role not in VALID_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Ruolo non valido: '{data.role}'. Validi: {VALID_ROLES}",
        )

    if current_user.role != "superadmin":
        # admin: forza il proprio gruppo e impedisce di creare admin/superadmin
        data.group_id = current_user.group_id
        if data.role in ROLES_ADMIN_OR_ABOVE:
            _disclosure.raise_disclosure(
                _disclosure.role_create_privilege_required()
            )

    if len(data.password) < 12:
        raise HTTPException(status_code=400, detail="La password deve essere lunga almeno 12 caratteri")

    existing = _users_dal.get_user_by_username(data.username)
    if existing:
        _disclosure.raise_disclosure(_disclosure.username_already_exists())

    user_id = f"usr_{uuid.uuid4().hex[:12]}"
    user = _users_dal.create_user(
        user_id=user_id, username=data.username,
        password_hash=hash_password(data.password),
        group_id=data.group_id, email=data.email, role=data.role,
    )
    return UserResponse(**_user_response_payload(user))


@users_router.put("/{user_id}", response_model=UserResponse)
@audit_action(action="user.update", target_param="user_id")
def update_user(user_id: str, data: dict,
                current_user: CurrentUser = Depends(require_admin)):
    """
    Aggiorna utente.
    superadmin → può cambiare role e group_id
    admin → solo email e is_active, solo nel proprio gruppo

    SECURITY hardening:
      - Whitelist dei campi consentiti per ruolo
      - Type checking di ogni campo (no coercion silenziosa)
      - Validazione email format (base)
      - Validazione role contro VALID_ROLES
      - Verifica esistenza group_id se passato
      - Self-demotion: un utente non può modificare il proprio ruolo né disabilitare se stesso
    """
    target = _users_dal.get_user(user_id)
    if not target:
        _disclosure.raise_disclosure(_disclosure.user_not_found())
    if current_user.role != "superadmin" and target.group_id != current_user.group_id:
        _disclosure.raise_disclosure(_disclosure.access_denied())

    # Fail-fast sui campi sensibili non gestiti qui: meglio errore esplicito
    # del drop silenzioso. Storicamente il frontend mandava `password` nel
    # payload di PUT e il backend lo scartava senza segnalare: l'utente
    # credeva di aver cambiato password mentre restava la vecchia
    if "password" in data:
        raise HTTPException(
            status_code=400,
            detail="Per cambiare password usa POST /users/{id}/reset-password",
        )
    # group_id non e' aggiornabile qui: spostare un utente tra gruppi
    # e' operazione separata (nessun endpoint implementato oggi
    if "group_id" in data and current_user.role != "superadmin":
        _disclosure.raise_disclosure(_disclosure.field_role_restricted_group_id())

    allowed = ["email", "is_active"]
    if current_user.role == "superadmin":
        allowed += ["role", "group_id"]

    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        raise HTTPException(status_code=400, detail="Nessun campo aggiornabile fornito")

    # Type checking strict (no coercion automatica
    if "email" in fields:
        if not isinstance(fields["email"], str) or not fields["email"]:
            raise HTTPException(status_code=400, detail="email deve essere una stringa non vuota")
        # Validazione formato base — non perfetta ma blocca payload assurdi
        if "@" not in fields["email"] or "." not in fields["email"].split("@")[-1]:
            raise HTTPException(status_code=400, detail="Formato email non valido")
    if "is_active" in fields:
        if not isinstance(fields["is_active"], bool):
            raise HTTPException(status_code=400, detail="is_active deve essere un booleano")
    if "role" in fields:
        if not isinstance(fields["role"], str):
            raise HTTPException(status_code=400, detail="role deve essere una stringa")
        if fields["role"] not in VALID_ROLES:
            raise HTTPException(
                status_code=400,
                detail=f"Ruolo non valido: '{fields['role']}'. Validi: {VALID_ROLES}",
            )
    if "group_id" in fields:
        if not isinstance(fields["group_id"], str) or not fields["group_id"]:
            raise HTTPException(status_code=400, detail="group_id deve essere una stringa non vuota")
        # Verifica che il gruppo esista — evita di mettere utenti in gruppi orfani
        if not _groups_dal.get_group(fields["group_id"]):
            raise HTTPException(status_code=400, detail=f"Gruppo '{fields['group_id']}' non trovato")

    # Self-demotion guard: un utente non può abbassarsi i privilegi né disabilitarsi
    if user_id == current_user.user_id:
        if "role" in fields and fields["role"] != current_user.role:
            _disclosure.raise_disclosure(_disclosure.self_modify_denied())
        if "is_active" in fields and fields["is_active"] is False:
            _disclosure.raise_disclosure(_disclosure.self_disable_denied())

    updated = _users_dal.update_user(user_id, **fields)
    if not updated:
        _disclosure.raise_disclosure(_disclosure.user_not_found())
    return UserResponse(**_user_response_payload(updated))


@users_router.post("/{user_id}/reset-password")
@audit_action(action="user.reset_password", target_param="user_id")
def reset_password(user_id: str, data: ResetPasswordRequest,
                   current_user: CurrentUser = Depends(require_admin)):
    """
    Reset password di un utente (senza richiedere la vecchia).
    superadmin → qualsiasi utente
    admin → solo utenti del proprio gruppo
    """
    target = _users_dal.get_user(user_id)
    # C10: response uniforme 403 per admin non-superadmin, sia che
    # l'utente non esista sia che esista ma fuori dal gruppo. Evita
    # user enumeration (admin malevolo non puo' distinguere i due
    # casi per mappare gli utenti di altri gruppi). Superadmin
    # mantiene 404 perche' ha visibilita' totale e la distinzione
    # e' informazione gia' accessibile per loro
    if not target or (
        current_user.role != "superadmin"
        and target.group_id != current_user.group_id
    ):
        # Anti-enum #3 — invariante encoded in disclosure.admin_target_not_visible
        # superadmin sees 404 USER_NOT_FOUND, admin sees uniform 403 ACCESS_DENIED
        _disclosure.raise_disclosure(
            _disclosure.admin_target_not_visible(
                is_superadmin=(current_user.role == "superadmin")
            )
        )

    if len(data.new_password) < 12:
        raise HTTPException(status_code=400, detail="La password deve essere lunga almeno 12 caratteri")

    _users_dal.update_user_password(user_id, hash_password(data.new_password))
    return {"ok": True}


@users_router.delete("/{user_id}", status_code=204)
@audit_action(action="user.delete", target_param="user_id")
def delete_user(user_id: str, current_user: CurrentUser = Depends(require_admin)):
    """
    Elimina utente.
    superadmin → qualsiasi utente
    admin → solo nel proprio gruppo, non se stesso
    """
    if user_id == current_user.user_id:
        _disclosure.raise_disclosure(_disclosure.self_delete_denied())

    target = _users_dal.get_user(user_id)
    # C10: stesso pattern di reset-password — anti-enum #3 invariant
    if not target or (
        current_user.role != "superadmin"
        and target.group_id != current_user.group_id
    ):
        _disclosure.raise_disclosure(
            _disclosure.admin_target_not_visible(
                is_superadmin=(current_user.role == "superadmin")
            )
        )

    if not _users_dal.delete_user(user_id):
        _disclosure.raise_disclosure(_disclosure.user_not_found())
