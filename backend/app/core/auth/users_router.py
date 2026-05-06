"""Auth users admin CRUD router — typed boundary.

BE-GO-READY-AUTH-ENGINE-003-USERS-ROUTER-EXTRACTION.

Estrae da `auth.py` i 5 endpoint admin CRUD `/auth/users*`:

- `GET    /auth/users`
- `POST   /auth/users`
- `PUT    /auth/users/{user_id}`
- `POST   /auth/users/{user_id}/reset-password`
- `DELETE /auth/users/{user_id}`

Boundary contract
=================
Il router NON conosce:

- token decode / disclosure-on-token (`get_current_user`,
  `decode_token`, `is_jti_blacklisted` — vivono in `auth.py`);
- password hashing low-level (`app.shared.password_hashing`); il
  router consuma solo l'helper re-esportato `hash_password`;
- rate-limit fallback (`app.core.auth.rate_limit_fallback`);
- MFA enroll/verify/disable (`app.core.auth.mfa`);
- ownership / require_*_owner (`app.core.auth.ownership`).

Behavior preserved 1:1
======================
- Stessi error code / messaggi italiani / status code / headers /
  anti-enumeration invariants definiti in
  `app.core.auth.disclosure`. Nessun cambio user-facing.
- Stessi audit log via `audit_action` decorator.
- Stessa dipendenza di accesso `require_admin` (admin + superadmin).
- Stessa logica di filtering per gruppo (admin), validazione
  payload, anti-enum #3 (admin reset/delete su target
  missing-or-out-of-group), self-modify/self-disable/self-delete
  guards.

KEEP_COMPAT
===========
`auth.py` ri-esporta `users_router` (vedi `auth.py` post-batch),
per cui `from app.core.auth.auth import users_router` continua a
funzionare. `main.py` lo importa direttamente dal nuovo modulo per
montarlo (`app.include_router(users_router, prefix="/api")`).

Go-ready mapping
================
    type UsersRouter struct { ... }
    func (r *UsersRouter) ListUsers(ctx, currentUser) ([]UserResponse, error)
    func (r *UsersRouter) CreateUser(ctx, payload, currentUser) (UserResponse, error)
    ...
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth import disclosure as _disclosure
# BE-GO-READY-AUTH-ENGINE-011-SHARED-CURRENT-USER-CONTRACT:
# `CurrentUser` consumed from the shared contract module
# (mirror of `groups_router.py` / Phase-7 routers). The three
# request/response DTOs (`ResetPasswordRequest`, `UserCreate`,
# `UserResponse`) stay on the models leaf for now; W-011 owns
# only the `CurrentUser` migration.
from app.core.auth.models import (
    ResetPasswordRequest,
    UserCreate,
    UserResponse,
)
from app.shared.contracts.auth import CurrentUser
from app.core.auth.roles import ROLES_ADMIN_OR_ABOVE, VALID_ROLES
from app.shared.audit_action import audit_action
from app.shared.password_hashing import hash_password

import app.db.db_service as db


__all__ = ["users_router"]


users_router = APIRouter(prefix="/auth/users", tags=["users"])
_security = HTTPBearer(auto_error=False)


def _get_current_user_proxy(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> CurrentUser:
    """Lazy proxy to avoid `users_router -> auth.py -> users_router` cycle.
    BE-GO-READY-AUTH-ENGINE-019: import moved to `dependencies` leaf.
    """
    from app.core.auth.dependencies import get_current_user

    return get_current_user(credentials)


def require_admin(
    current_user: CurrentUser = Depends(_get_current_user_proxy),
) -> CurrentUser:
    """Lazy proxy preserving the existing `auth.require_admin` behavior."""
    from app.core.auth.dependencies import require_admin as _require_admin

    return _require_admin(current_user)


# --- Gestione utenti (admin only) ---


@users_router.get("", response_model=list[UserResponse])
def list_users(current_user: CurrentUser = Depends(require_admin)):
    """
    Lista utenti.
    superadmin → tutti gli utenti di tutti i gruppi
    admin      → solo utenti del proprio gruppo
    """
    if current_user.role == "superadmin":
        # Superadmin: tutti gli utenti con una singola query (no N+1 per gruppo).
        # BE-GO-READY-DB-ENGINE-006-FACADE-PROD-PURGE-NEXT: leaf import
        # (`app.db.config.users.list_all_users`) replaces the prior
        # `_cfg_db.fetchall(...)` raw-SQL shim through the facade. The
        # leaf helper preserves the `SELECT * FROM users ORDER BY username`
        # shape used by `UserResponse.model_fields` filtering below, so
        # the response payload is byte-identical.
        from app.db.config.users import list_all_users
        users = list_all_users()
    else:
        users = db.list_users(current_user.group_id)
    return [UserResponse(**{k: u[k] for k in UserResponse.model_fields if k in u}) for u in users]


@users_router.post("", response_model=UserResponse, status_code=201)
@audit_action(action="user.create")
def create_user(data: UserCreate, current_user: CurrentUser = Depends(require_admin)):
    """
    Crea nuovo utente.
    superadmin → qualsiasi gruppo, qualsiasi ruolo (viewer/user/admin/superadmin)
    admin      → solo nel proprio gruppo, solo ruolo viewer o user
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

    existing = db.get_user_by_username(data.username)
    if existing:
        _disclosure.raise_disclosure(_disclosure.username_already_exists())

    user_id = f"usr_{uuid.uuid4().hex[:12]}"
    user = db.create_user(
        user_id=user_id, username=data.username,
        password_hash=hash_password(data.password),
        group_id=data.group_id, email=data.email, role=data.role,
    )
    return UserResponse(**{k: user[k] for k in UserResponse.model_fields if k in user})


@users_router.put("/{user_id}", response_model=UserResponse)
@audit_action(action="user.update", target_param="user_id")
def update_user(user_id: str, data: dict,
                current_user: CurrentUser = Depends(require_admin)):
    """
    Aggiorna utente.
    superadmin → può cambiare role e group_id
    admin      → solo email e is_active, solo nel proprio gruppo

    SECURITY hardening:
      - Whitelist dei campi consentiti per ruolo
      - Type checking di ogni campo (no coercion silenziosa)
      - Validazione email format (base)
      - Validazione role contro VALID_ROLES
      - Verifica esistenza group_id se passato
      - Self-demotion: un utente non può modificare il proprio ruolo né disabilitare se stesso
    """
    target = db.get_user(user_id)
    if not target:
        _disclosure.raise_disclosure(_disclosure.user_not_found())
    if current_user.role != "superadmin" and target.get("group_id") != current_user.group_id:
        _disclosure.raise_disclosure(_disclosure.access_denied())

    # Fail-fast sui campi sensibili non gestiti qui: meglio errore esplicito
    # del drop silenzioso. Storicamente il frontend mandava `password` nel
    # payload di PUT e il backend lo scartava senza segnalare: l'utente
    # credeva di aver cambiato password mentre restava la vecchia.
    if "password" in data:
        raise HTTPException(
            status_code=400,
            detail="Per cambiare password usa POST /users/{id}/reset-password",
        )
    # group_id non e' aggiornabile qui: spostare un utente tra gruppi
    # e' operazione separata (nessun endpoint implementato oggi).
    if "group_id" in data and current_user.role != "superadmin":
        _disclosure.raise_disclosure(_disclosure.field_role_restricted_group_id())

    allowed = ["email", "is_active"]
    if current_user.role == "superadmin":
        allowed += ["role", "group_id"]

    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        raise HTTPException(status_code=400, detail="Nessun campo aggiornabile fornito")

    # Type checking strict (no coercion automatica)
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
        if not db.get_group(fields["group_id"]):
            raise HTTPException(status_code=400, detail=f"Gruppo '{fields['group_id']}' non trovato")

    # Self-demotion guard: un utente non può abbassarsi i privilegi né disabilitarsi
    if user_id == current_user.user_id:
        if "role" in fields and fields["role"] != current_user.role:
            _disclosure.raise_disclosure(_disclosure.self_modify_denied())
        if "is_active" in fields and fields["is_active"] is False:
            _disclosure.raise_disclosure(_disclosure.self_disable_denied())

    updated = db.update_user(user_id, **fields)
    if not updated:
        _disclosure.raise_disclosure(_disclosure.user_not_found())
    return UserResponse(**{k: updated[k] for k in UserResponse.model_fields if k in updated})


@users_router.post("/{user_id}/reset-password")
@audit_action(action="user.reset_password", target_param="user_id")
def reset_password(user_id: str, data: ResetPasswordRequest,
                   current_user: CurrentUser = Depends(require_admin)):
    """
    Reset password di un utente (senza richiedere la vecchia).
    superadmin → qualsiasi utente
    admin      → solo utenti del proprio gruppo
    """
    target = db.get_user(user_id)
    # C10: response uniforme 403 per admin non-superadmin, sia che
    # l'utente non esista sia che esista ma fuori dal gruppo. Evita
    # user enumeration (admin malevolo non puo' distinguere i due
    # casi per mappare gli utenti di altri gruppi). Superadmin
    # mantiene 404 perche' ha visibilita' totale e la distinzione
    # e' informazione gia' accessibile per loro.
    if not target or (
        current_user.role != "superadmin"
        and target.get("group_id") != current_user.group_id
    ):
        # Anti-enum #3 — invariante encoded in disclosure.admin_target_not_visible:
        # superadmin sees 404 USER_NOT_FOUND, admin sees uniform 403 ACCESS_DENIED.
        _disclosure.raise_disclosure(
            _disclosure.admin_target_not_visible(
                is_superadmin=(current_user.role == "superadmin")
            )
        )

    if len(data.new_password) < 12:
        raise HTTPException(status_code=400, detail="La password deve essere lunga almeno 12 caratteri")

    db.update_user_password(user_id, hash_password(data.new_password))
    return {"ok": True}


@users_router.delete("/{user_id}", status_code=204)
@audit_action(action="user.delete", target_param="user_id")
def delete_user(user_id: str, current_user: CurrentUser = Depends(require_admin)):
    """
    Elimina utente.
    superadmin → qualsiasi utente
    admin      → solo nel proprio gruppo, non se stesso
    """
    if user_id == current_user.user_id:
        _disclosure.raise_disclosure(_disclosure.self_delete_denied())

    target = db.get_user(user_id)
    # C10: stesso pattern di reset-password — anti-enum #3 invariant.
    if not target or (
        current_user.role != "superadmin"
        and target.get("group_id") != current_user.group_id
    ):
        _disclosure.raise_disclosure(
            _disclosure.admin_target_not_visible(
                is_superadmin=(current_user.role == "superadmin")
            )
        )

    if not db.delete_user(user_id):
        _disclosure.raise_disclosure(_disclosure.user_not_found())
