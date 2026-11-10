"""
Auth users admin CRUD router — typed boundary.

NOTE: NON usare `from __future__ import annotations` qui. Pydantic v2 +
FastAPI faticano a risolvere ForwardRef dei BaseModel passati come body
parameter (es. `def create_user(data: UserCreate)`): senza PEP 563 le
annotation sono oggetti reali a load-time e FastAPI sceglie Body
implicito senza ambiguita`. Con PEP 563 la stringa "UserCreate" diventa
un Annotated[ForwardRef, Query(PydanticUndefined)] che fallisce lo
schema generation con `TypeAdapter is not fully defined`.
"""

import uuid
from collections.abc import Mapping
from typing import Annotated, NoReturn
from typing_extensions import TypedDict

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError

from app.core.auth import disclosure as _disclosure
from app.core.auth.exceptions import UsernameInvalidError, UsernameTakenError
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http
from app.core.auth.models import (
    ResetPasswordRequest,
    UserCreate,
    UserResponse,
    UserUpdateRequest,
)
from app.shared.contracts.auth import CurrentUser
from app.core.auth.roles import ROLES_ADMIN_OR_ABOVE, VALID_ROLES
from app.observability.audit_action import audit_action
from app.core.security.password_hashing import hash_password

from app.core.auth import db as _groups_dal
from app.core.auth import db as _users_dal
from app.core.auth import user_references as _user_references


__all__ = ["users_router"]
UserUpdateFields = Mapping[str, object]


users_router = APIRouter(prefix="/auth/users", tags=["users"])
_security = HTTPBearer(auto_error=False)


class UserResponsePayload(TypedDict):
    id: str
    username: str
    email: str | None
    group_id: str
    role: str
    is_active: bool
    created_at: str


def _user_response_payload(user: _users_dal.UserRow) -> UserResponsePayload:
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
    from app.core.auth.dependencies import get_current_user

    return get_current_user(credentials)


def require_admin(current_user: CurrentUser = Depends(_get_current_user_proxy)) -> CurrentUser:
    from app.core.auth.dependencies import require_admin as _require_admin

    return _require_admin(current_user)


def _user_update_fields(data: UserUpdateRequest | UserUpdateFields) -> UserUpdateFields:
    if isinstance(data, dict):
        try:
            return UserUpdateRequest.model_validate(data).model_dump(exclude_unset=True)
        except ValidationError as exc:
            message = str(exc)
            if "reset-password" in message:
                raise HTTPException(status_code=400, detail=message) from exc
            if "Ruolo non valido" in message:
                raise HTTPException(status_code=400, detail=message) from exc
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
    return data.model_dump(exclude_unset=True)


@users_router.get("", response_model=list[UserResponse])
def list_users(
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: CurrentUser = Depends(require_admin),
):
    """
    Lista utenti.
    superadmin → tutti gli utenti di tutti i gruppi
    admin → solo utenti del proprio gruppo
    """
    if current_user.role == "superadmin":
        users = _users_dal.list_all_users(limit=limit, offset=offset)
    else:
        users = _users_dal.list_users(
            current_user.group_id, limit=limit, offset=offset,
        )
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

    # Regola dello username (shared.contracts.auth): forma valida e unicita'
    # sulla forma normalizzata. La applica create_user, unico punto di
    # scrittura; qui la si verifica prima per non calcolare l'hash invano.
    try:
        _users_dal.assert_username_writable(data.username)
        user_id = f"usr_{uuid.uuid4().hex[:12]}"
        user = _users_dal.create_user(
            user_id=user_id, username=data.username,
            password_hash=hash_password(data.password),
            group_id=data.group_id, email=data.email, role=data.role,
        )
    except UsernameTakenError:
        _disclosure.raise_disclosure(_disclosure.username_already_exists())
    except UsernameInvalidError as exc:
        raise_typed_http(error_codes.VALIDATION_ERROR, status_code=400, message=str(exc),
                         details={"reason": "username_invalid"})
    return UserResponse(**_user_response_payload(user))


@users_router.put("/{user_id}", response_model=UserResponse)
@audit_action(action="user.update", target_param="user_id")
def update_user(user_id: str, data: UserUpdateRequest,
                current_user: CurrentUser = Depends(require_admin)):
    """
    Aggiorna utente.
    superadmin → può cambiare role e group_id
    admin → solo email e is_active, solo nel proprio gruppo

    Body schema: `UserUpdateRequest` (Pydantic, extra="forbid"). Type
    check + format validation gestiti dal modello; il router applica solo
    la whitelist per-ruolo, il check di esistenza del group_id e i
    self-modify guard.

    Il body resta tipizzato in OpenAPI e la validazione non viene duplicata
    manualmente nel router.
    """
    target = _users_dal.get_user(user_id)
    if not target:
        _disclosure.raise_disclosure(_disclosure.user_not_found())
    if current_user.role != "superadmin" and target.group_id != current_user.group_id:
        _disclosure.raise_disclosure(_disclosure.access_denied())

    # `exclude_unset=True` distingue "non passato" da "passato come None":
    # solo i campi presenti nel body originale finiscono in `data_dict`.
    data_dict = _user_update_fields(data)

    # group_id non e' aggiornabile da admin non-super (whitelist per-ruolo,
    # non check di shape — resta nel router).
    if "group_id" in data_dict and current_user.role != "superadmin":
        _disclosure.raise_disclosure(_disclosure.field_role_restricted_group_id())

    allowed = {"email", "is_active"}
    if current_user.role == "superadmin":
        allowed |= {"role", "group_id"}

    fields = {k: v for k, v in data_dict.items() if k in allowed}
    if not fields:
        raise HTTPException(status_code=400, detail="Nessun campo aggiornabile fornito")

    # Esistenza del group_id richiede lookup DB → resta nel router
    # (validazione di shape gia' fatta dal modello).
    if "group_id" in fields and not _groups_dal.get_group(fields["group_id"]):
        raise HTTPException(
            status_code=400,
            detail=f"Gruppo '{fields['group_id']}' non trovato",
        )

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


def _raise_user_has_history(username: str, references: list[_user_references.UserReferenceCount]) -> NoReturn:
    raise_typed_http(
        error_codes.USER_HAS_HISTORY,
        status_code=409,
        message=_user_references.history_message(username, references),
        details={"username": username, "references": references},
    )


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

    # Lo storico non si perde e non si riscrive (09/11/2026): con righe che
    # referenziano l'utente senza ON DELETE, 409 con i conteggi per tipo.
    references = _users_dal.count_user_references(user_id)
    if references:
        _raise_user_has_history(target.username, references)
    try:
        deleted = _users_dal.delete_user(user_id)
    except _user_references.UserHasHistoryError:
        _raise_user_has_history(target.username, [])
    if not deleted:
        _disclosure.raise_disclosure(_disclosure.user_not_found())


def require_superadmin(current_user: CurrentUser = Depends(_get_current_user_proxy)) -> CurrentUser:
    """Stessa regola di ``dependencies.require_superadmin``."""
    if current_user.role != "superadmin":
        _disclosure.raise_disclosure(_disclosure.role_superadmin_required())
    return current_user


@users_router.post("/{user_id}/delete-keeping-history", status_code=204)
@audit_action(action="user.delete_keeping_history", target_param="user_id")
def delete_user_keeping_history(user_id: str, current_user: CurrentUser = Depends(require_superadmin)) -> None:
    """
    Elimina l'utente MANTENENDO lo storico (10/11/2026). Solo superadmin.

    La riga resta come «lapide» (``auth.db.tombstone_user``, una
    transazione): ogni chiave esterna dello storico resta valida; lo username
    originale passa in ``deleted_username`` e torna libero; password, email e
    MFA si cancellano; l'utente non si autentica piu' in nessun modo e sparisce
    da liste, selettori e conteggi; lo storico lo mostra come «utente
    eliminato (<username>)». Vale con o senza storico.

    Protezioni: non se stessi; l'ultimo superadmin non si puo' eliminare per
    costruzione (chi elimina e' un superadmin attivo e non e' il bersaglio).
    """
    if user_id == current_user.user_id:
        _disclosure.raise_disclosure(_disclosure.self_delete_denied())
    if not _users_dal.get_user(user_id) or not _users_dal.tombstone_user(user_id):
        _disclosure.raise_disclosure(_disclosure.user_not_found())
