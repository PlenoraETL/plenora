"""Auth shared contract: pure DTOs, role constants, and pure policy."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, StrictBool, field_validator, model_validator


VALID_ROLES: tuple[str, ...] = (
    "esecutore",
    "viewer",
    "user",
    "admin",
    "superadmin",
)

ROLES_ADMIN_OR_ABOVE: tuple[str, ...] = ("admin", "superadmin")
ROLES_ABOVE_VIEWER: tuple[str, ...] = ("user", "admin", "superadmin")
ROLES_KIOSK_ONLY: tuple[str, ...] = ("esecutore",)


# REGOLA degli username (input «Utente che esegue», correzione dopo la
# conferma, punto 2). Lo username e' un'identita' che finisce nei filtri delle
# catene (``shared.execution.runtime_identity``) e si usa ESATTO, senza
# trasformazioni. Quindi in OGNI scrittura di un utente (``auth.db.create_user``:
# API di creazione, bootstrap dell'amministratore degli installer, seed):
# - forma: niente spazi iniziali o finali, niente caratteri di controllo,
#   non vuoto (:func:`username_form_error`);
# - unicita' sulla forma normalizzata (trim + casefold,
#   :func:`normalized_username`): «Mario» e «mario » non possono coesistere,
#   perche' molti database confrontano senza maiuscole o senza spazi finali.


def normalized_username(username: str) -> str:
    return str(username).strip().casefold()


def username_form_error(username: object) -> str | None:
    """Motivo per cui ``username`` non e' accettabile, ``None`` se lo e'."""
    if not isinstance(username, str) or not username.strip():
        return "Lo username non puo' essere vuoto"
    if username != username.strip():
        return "Lo username non puo' iniziare o finire con spazi"
    if any(ord(char) < 32 or ord(char) == 127 or 0x80 <= ord(char) < 0xA0 for char in username):
        return "Lo username non puo' contenere caratteri di controllo"
    return None


# REGOLA dell'utente eliminato «a lapide» (10/11/2026). Eliminare un utente
# con storico non cancella la riga: ``users.deleted_at`` e' valorizzato, lo
# username originale passa in ``users.deleted_username`` e ``username``
# diventa :func:`tombstone_username` (libera il nome per un utente nuovo).
# Due sole letture della tabella ``users``:
# - OPERATIVA (autenticazione, liste, selettori, conteggi): solo le righe
#   con :data:`NOT_DELETED_SQL`; una lapide non esiste;
# - STORICA (chi ha scritto il commento, chi ha salvato la versione, ...):
#   il nome si mostra SOLO con :func:`user_display_name_sql`, che per una
#   lapide da' «utente eliminato (<username originale>)».
# Il test ``tests2/auth/test_deleted_user_contract.py`` enumera ogni SQL del
# backend che legge ``users`` e la confronta con la tabella dichiarativa
# ``app.core.auth.deleted_users.USERS_SQL_SITES``.
NOT_DELETED_SQL = "deleted_at IS NULL"
DELETED_USER_LABEL = "utente eliminato"
TOMBSTONE_USERNAME_PREFIX = "eliminato:"


def tombstone_username(user_id: str) -> str:
    """Username della lapide: unico (contiene l'id) e diverso da qualunque
    nome scelto da un amministratore che non lo copi apposta."""
    return f"{TOMBSTONE_USERNAME_PREFIX}{user_id}"


def user_display_name_sql(alias: str, live_name_sql: str | None = None) -> str:
    """Espressione SQL del nome nello storico per la riga ``users`` con alias
    ``alias`` (unita con LEFT JOIN). ``live_name_sql``: il nome da mostrare
    per un utente non eliminato (default ``<alias>.username``; es. il nome
    registrato al momento del fatto). Una riga assente (LEFT JOIN vuoto) da'
    ``live_name_sql``, come prima."""
    if not alias.isidentifier():
        raise ValueError(f"alias SQL non valido: {alias!r}")
    live = live_name_sql or f"{alias}.username"
    label = DELETED_USER_LABEL.replace("'", "''")
    return (
        f"CASE WHEN {alias}.{NOT_DELETED_SQL} THEN {live} "
        f"WHEN NULLIF(btrim({alias}.deleted_username), '') IS NULL THEN '{label}' "
        f"ELSE '{label} (' || btrim({alias}.deleted_username) || ')' END"
    )


class LoginRequest(BaseModel):
    username: str
    password: str
    # None is valid for users without MFA; enrolled users must provide a code.
    mfa_code: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str
    group_id: str
    role: str
    expires_in: int


class CurrentUser(BaseModel):
    user_id: str
    username: str
    group_id: str
    role: str


class UserCreate(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    group_id: str
    role: str = "user"


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class UserResponse(BaseModel):
    id: str
    username: str
    email: Optional[str]
    group_id: str
    role: str
    is_active: bool
    created_at: str


class ResetPasswordRequest(BaseModel):
    new_password: str


class UserUpdateRequest(BaseModel):
    """Body tipizzato per `PUT /auth/users/{user_id}` (admin update)."""

    email: Optional[str] = None
    # StrictBool: rifiuta coercion da string ("yes", "false") - `is_active`
    # e' un security flag e una coercion silenziosa puo' risultare in
    # account disattivati per errore.
    is_active: Optional[StrictBool] = None
    role: Optional[str] = None
    group_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _reject_password(cls, values):
        if isinstance(values, dict) and "password" in values:
            raise ValueError(
                "Per cambiare password usa POST /users/{id}/reset-password"
            )
        return values

    @field_validator("email")
    @classmethod
    def _email_format(cls, v):
        if v is None:
            return v
        if not v:
            raise ValueError("email deve essere una stringa non vuota")
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Formato email non valido")
        return v

    @field_validator("role")
    @classmethod
    def _role_allowed(cls, v):
        if v is None:
            return v
        if v not in VALID_ROLES:
            raise ValueError(
                f"Ruolo non valido: '{v}'. Validi: {VALID_ROLES}"
            )
        return v

    @field_validator("group_id")
    @classmethod
    def _group_id_nonempty(cls, v):
        if v is None:
            return v
        if not v:
            raise ValueError("group_id deve essere una stringa non vuota")
        return v


class GroupCreate(BaseModel):
    group_id: str
    name: str
    description: str = ""


class MFAEnrollResponse(BaseModel):
    secret: str
    provisioning_uri: str
    qr_code_png_base64: str


class MFAVerifyRequest(BaseModel):
    code: str


class MFADisableRequest(BaseModel):
    code: str


class MFAStatusResponse(BaseModel):
    enabled: bool
    enrolled_at: Optional[str] = None
    last_verified_at: Optional[str] = None


class GroupResponse(BaseModel):
    group_id: str
    name: str
    description: str = ""


def group_filter(current_user: CurrentUser) -> Optional[str]:
    """Return the group_id filter for a user, or None for superadmin."""
    if current_user.role == "superadmin":
        return None
    return current_user.group_id


for _m in (
    LoginRequest,
    TokenResponse,
    CurrentUser,
    UserCreate,
    ChangePasswordRequest,
    UserResponse,
    ResetPasswordRequest,
    UserUpdateRequest,
    GroupCreate,
    MFAEnrollResponse,
    MFAVerifyRequest,
    MFADisableRequest,
    MFAStatusResponse,
    GroupResponse,
):
    _m.model_rebuild()


__all__ = [
    "CurrentUser",
    "VALID_ROLES",
    "ROLES_ABOVE_VIEWER",
    "ROLES_ADMIN_OR_ABOVE",
    "ROLES_KIOSK_ONLY",
    "group_filter",
    "LoginRequest",
    "TokenResponse",
    "UserCreate",
    "ChangePasswordRequest",
    "UserResponse",
    "ResetPasswordRequest",
    "UserUpdateRequest",
    "GroupCreate",
    "GroupResponse",
    "MFAEnrollResponse",
    "MFAVerifyRequest",
    "MFADisableRequest",
    "MFAStatusResponse",
]
