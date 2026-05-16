"""JWT authentication router and public auth-domain exports."""

from datetime import datetime, timezone
from typing import Optional, TypedDict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

# Import esplicito di app.config per side effect: garantisce che backend/.env
# sia stato caricato via python-dotenv PRIMA che leggiamo JWT_SECRET_KEY a
# livello di modulo. Senza questo, se auth.py venisse importato prima di
# app.config, la lettura di os.getenv("JWT_SECRET_KEY") sotto sarebbe fatta
# su un environment in cui il .env non è ancora stato applicato
from app.config import settings as _settings
from app.core.auth import db
from app.core.auth import disclosure as _disclosure
from app.shared import login_rate_limiter as _rl
from app.observability.audit_action import audit_action
from app.shared.request_utils import get_client_ip
from app.shared.token_blacklist import blacklist_jti
from app.utils.logger import get_logger


from app.core.auth.roles import (  # noqa: F401 (explicit public auth API)
    VALID_ROLES,
    ROLES_ABOVE_VIEWER,
    ROLES_ADMIN_OR_ABOVE,
    ROLES_KIOSK_ONLY,
)

logger = get_logger(__name__)

# Login rate-limit constants stay module-level because security tests
# monkeypatch them directly through this public auth facade.
from app.core.auth.rate_limit_fallback import (
    RateLimitFallbackTriggered,
    _STORE as _rate_limit_fallback_store,
    record_failed_login_in_memory as _record_failed_login_in_memory,
    clear_failed_logins_in_memory as _clear_failed_logins_in_memory,
)

LOGIN_MAX_ATTEMPTS    = int(_settings.get("LOGIN_MAX_ATTEMPTS"))
LOGIN_WINDOW_SECONDS  = int(_settings.get("LOGIN_WINDOW_SECONDS"))
LOGIN_LOCKOUT_SECONDS = int(_settings.get("LOGIN_LOCKOUT_SECONDS"))


def _check_rate_limit_in_memory(ip: str):
    """Auth-level in-memory rate-limit adapter.

    Legge le tre costanti monkeypatch-ate a livello di `auth.py`,
    delega al fallback estratto, cattura `RateLimitFallbackTriggered`
    e converte in disclosure (`disclosure.rate_limit_exceeded`).
    Il clamp `Retry-After >= 1` vive in `disclosure`.
    """
    try:
        _rate_limit_fallback_store.check(
            ip,
            max_attempts=LOGIN_MAX_ATTEMPTS,
            window_seconds=LOGIN_WINDOW_SECONDS,
            lockout_seconds=LOGIN_LOCKOUT_SECONDS,
        )
    except RateLimitFallbackTriggered as triggered:
        _disclosure.raise_disclosure(
            _disclosure.rate_limit_exceeded(triggered.retry_after)
        )


def _check_rate_limit(ip: str):
    """Wrapper R-01: tenta Redis, fallback in-memory.

    Le costanti LOGIN_* vengono rilette ad ogni chiamata per mantenere
    monkeypatch-friendly i test esistenti.
    """
    _rl.check_rate_limit(
        ip,
        max_attempts=LOGIN_MAX_ATTEMPTS,
        window_seconds=LOGIN_WINDOW_SECONDS,
        lockout_seconds=LOGIN_LOCKOUT_SECONDS,
        fallback=_check_rate_limit_in_memory,
    )


def _record_failed_login(ip: str):
    _rl.record_failed_login(
        ip,
        window_seconds=LOGIN_WINDOW_SECONDS,
        fallback=_record_failed_login_in_memory,
    )


def _clear_failed_logins(ip: str):
    _rl.clear_failed_logins(
        ip,
        fallback=_clear_failed_logins_in_memory,
    )


# Keep these as explicit public auth facade exports for existing callers.
from app.core.auth.jwt_token import (  # noqa: F401 (explicit public auth API)
    ALGORITHM,
    ACCESS_TOKEN_EXPIRE_HOURS,
    SECRET_KEY,
    create_access_token,
    decode_token,
)


from app.core.auth.models import (  # noqa: F401 (explicit public auth API)
    LoginRequest,
    TokenResponse,
    CurrentUser,
    ChangePasswordRequest,
    UserResponse,
    GroupResponse,
    MFAEnrollResponse,
    MFAVerifyRequest,
    MFADisableRequest,
    MFAStatusResponse,
)

class UserResponsePayload(TypedDict):
    id: str
    username: str
    email: str | None
    group_id: str
    role: str
    is_active: bool
    created_at: str


def _user_response_payload(user: db.UserRow) -> UserResponsePayload:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "group_id": user.group_id,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at,
    }


from app.shared.password_hashing import (  # noqa: F401 (explicit public auth API)
    _ph,
    hash_password,
    verify_password,
    needs_rehash,
    _DUMMY_PASSWORD_HASH,
)

# Public auth API exports for existing callers; new domain imports should use
# `app.core.auth.dependencies` directly.

from app.core.auth.dependencies import (  # noqa: F401 (explicit public auth API)
    _bearer_scheme,
    get_current_user,
    require_admin,
    require_not_esecutore,
    require_superadmin,
    require_user_or_above,
)

# Owner dependencies remain part of the explicit auth API. Domain
# modules should import them from `app.core.auth.owner_dependencies`
# directly.

from app.core.auth.owner_dependencies import (  # noqa: F401 (explicit public auth API)
    require_file_owner,
    require_project_owner,
    require_schedule_owner,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
@audit_action(action="auth.login")
def login(data: LoginRequest, request: Request):
    """Login con username/password → JWT token."""
    client_ip = get_client_ip(request)
    _check_rate_limit(client_ip)

    user = db.get_user_by_username(data.username)
    # Always verify against a real hash to avoid username-enumeration timing leaks.
    password_hash = user.password_hash if user else _DUMMY_PASSWORD_HASH
    password_ok = verify_password(data.password, password_hash)
    if not user or not password_ok:
        _record_failed_login(client_ip)
        _disclosure.raise_disclosure(_disclosure.invalid_credentials())
    if not user.is_active:
        _disclosure.raise_disclosure(_disclosure.account_disabled())

    # MFA challenge happens only after password verification; IP rate limiting
    # bounds the known disclosure that an account has MFA enabled.
    from app.core.auth import mfa as _mfa
    if _mfa.is_enabled(user.id):
        if not data.mfa_code:
            _disclosure.raise_disclosure(_disclosure.mfa_required())
        if not _mfa.verify_code(user.id, data.mfa_code):
            _record_failed_login(client_ip)
            _disclosure.raise_disclosure(_disclosure.mfa_invalid())

    _clear_failed_logins(client_ip)

    # Password hash upgrades are best-effort and must not deny a valid login.
    if needs_rehash(user.password_hash):
        try:
            db.update_user_password(user.id, hash_password(data.password))
        except Exception as e:
            logger.warning(
                f"Rehash password fallito per {user.username}: {e}",
                op="login_rehash",
            )

    token = create_access_token(
        user_id=user.id, username=user.username,
        group_id=user.group_id, role=user.role,
    )
    logger.info(f"Login: {user.username} (gruppo: {user.group_id})", op="login")
    return TokenResponse(
        access_token=token, user_id=user.id, username=user.username,
        group_id=user.group_id, role=user.role,
        expires_in=ACCESS_TOKEN_EXPIRE_HOURS * 3600,
    )


@router.post("/logout")
@audit_action(action="auth.logout")
def logout(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
):
    """Revoca il token corrente aggiungendo il suo jti alla blacklist.

    - TTL della blacklist = `exp - now` → auto-cleanup a scadenza.
    - Idempotente: chiamarlo 2 volte va bene (seconda volta la SETEX
      riscrive la stessa chiave con stesso valore).
    - Token pre-rollout (senza claim jti) → no blacklist possibile, il
      client comunque considera il suo logout "avvenuto" lato UI, ma
      il token resta valido fino alla sua exp naturale. Ritorniamo
      `blacklisted=False` per trasparenza.
    - Redis down → 503 (fail-loud): il client deve sapere se la revoca
      e' effettiva per non cambiare security posture inconsapevolmente.
    """
    if credentials is None:
        _disclosure.raise_disclosure(_disclosure.token_missing())
    payload = decode_token(credentials.credentials)
    if payload is None:
        _disclosure.raise_disclosure(_disclosure.token_invalid())
    jti = payload.get("jti")
    exp = payload.get("exp")
    if not jti or not exp:
        logger.info("Logout su token legacy senza jti, no blacklist", op="logout_legacy")
        return {"ok": True, "blacklisted": False}

    now_ts = int(datetime.now(timezone.utc).timestamp())
    ttl = int(exp) - now_ts
    if ttl <= 0:
        return {"ok": True, "blacklisted": False}

    if not blacklist_jti(jti, ttl):
        _disclosure.raise_disclosure(_disclosure.revocation_unavailable())
    logger.info(f"Logout: jti blacklistato, ttl={ttl}s", op="logout")
    return {"ok": True, "blacklisted": True, "ttl": ttl}


@router.get("/me", response_model=UserResponse)
def get_me(current_user: CurrentUser = Depends(get_current_user)):
    """Profilo dell'utente corrente. Qualsiasi autenticato."""
    user = db.get_user(current_user.user_id)
    if not user:
        _disclosure.raise_disclosure(_disclosure.user_not_found())
    return UserResponse(**_user_response_payload(user))


@router.post("/change-password")
@audit_action(action="auth.change_password")
def change_password(
    data: ChangePasswordRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Cambia la propria password. Richiede di conoscere la password attuale.
    Qualsiasi utente autenticato (incluso viewer) può cambiare la propria.
    """
    user = db.get_user(current_user.user_id)
    if not user:
        _disclosure.raise_disclosure(_disclosure.user_not_found())

    if not verify_password(data.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Password attuale errata")

    if len(data.new_password) < 12:
        raise HTTPException(status_code=400, detail="La nuova password deve essere lunga almeno 12 caratteri")

    db.update_user_password(current_user.user_id, hash_password(data.new_password))
    logger.info(f"Password cambiata: {current_user.username}", op="change_password")
    return {"ok": True}


@router.get("/mfa/status", response_model=MFAStatusResponse)
def get_mfa_status(current_user: CurrentUser = Depends(get_current_user)):
    """Stato MFA dell'utente corrente. Qualsiasi autenticato."""
    from app.core.auth import mfa as _mfa
    return MFAStatusResponse(**_mfa.status(current_user.user_id))


@router.post("/mfa/enroll", response_model=MFAEnrollResponse)
@audit_action(action="mfa.enroll")
def mfa_enroll(current_user: CurrentUser = Depends(get_current_user)):
    """
    Genera un nuovo secret TOTP (enrollment pendente).

    Sovrascrive qualsiasi riga esistente: se l'utente aveva MFA attiva,
    perde l'attivazione finche' non chiama /mfa/verify col nuovo codice.
    Use-case: ri-enrollment dopo perdita device.
    """
    from app.core.auth import mfa as _mfa
    data = _mfa.generate_enrollment(current_user.user_id, current_user.username)
    return MFAEnrollResponse(**data)


@router.post("/mfa/verify")
@audit_action(action="mfa.verify")
def mfa_verify(
    data: MFAVerifyRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Conferma l'enrollment attivando MFA (enabled=TRUE). Richiede un
    codice TOTP valido dal secret ricevuto in /mfa/enroll.
    """
    from app.core.auth import mfa as _mfa
    if not _mfa.verify_enrollment(current_user.user_id, data.code):
        _disclosure.raise_disclosure(_disclosure.mfa_code_invalid())
    return {"ok": True, "enabled": True}


@router.post("/mfa/disable")
@audit_action(action="mfa.disable")
def mfa_disable(
    data: MFADisableRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Disabilita MFA. Richiede codice TOTP valido (proof-of-possession):
    non basta essere autenticati per disattivare, bisogna anche avere
    l'authenticator. Questo limita il danno da JWT rubati.
    """
    from app.core.auth import mfa as _mfa
    if not _mfa.disable(current_user.user_id, data.code):
        _disclosure.raise_disclosure(_disclosure.mfa_code_invalid())
    return {"ok": True, "enabled": False}


from app.core.auth.users_router import users_router  # noqa: F401


from app.core.auth.groups_router import groups_router  # noqa: F401


__all__ = [
    "ACCESS_TOKEN_EXPIRE_HOURS",
    "ALGORITHM",
    "ChangePasswordRequest",
    "CurrentUser",
    "GroupResponse",
    "LOGIN_LOCKOUT_SECONDS",
    "LOGIN_MAX_ATTEMPTS",
    "LOGIN_WINDOW_SECONDS",
    "LoginRequest",
    "MFADisableRequest",
    "MFAEnrollResponse",
    "MFAStatusResponse",
    "MFAVerifyRequest",
    "ROLES_ABOVE_VIEWER",
    "ROLES_ADMIN_OR_ABOVE",
    "ROLES_KIOSK_ONLY",
    "SECRET_KEY",
    "TokenResponse",
    "UserResponse",
    "VALID_ROLES",
    "_DUMMY_PASSWORD_HASH",
    "_bearer_scheme",
    "_check_rate_limit",
    "_check_rate_limit_in_memory",
    "_clear_failed_logins",
    "_ph",
    "_record_failed_login",
    "create_access_token",
    "decode_token",
    "get_current_user",
    "get_me",
    "groups_router",
    "hash_password",
    "login",
    "logout",
    "needs_rehash",
    "require_admin",
    "require_file_owner",
    "require_not_esecutore",
    "require_project_owner",
    "require_schedule_owner",
    "require_superadmin",
    "require_user_or_above",
    "router",
    "users_router",
    "verify_password",
]
