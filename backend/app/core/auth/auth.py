"""
PLENORA-ETL — Autenticazione JWT

Modello RBAC:
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# Import esplicito di app.config per side effect: garantisce che backend/.env
# sia stato caricato via python-dotenv PRIMA che leggiamo JWT_SECRET_KEY a
# livello di modulo. Senza questo, se auth.py venisse importato prima di
# app.config, la lettura di os.getenv("JWT_SECRET_KEY") sotto sarebbe fatta
# su un environment in cui il .env non è ancora stato applicato
import app.config  # noqa: F401
from app.config import settings as _settings
from app.core.auth import db
from app.core.auth import disclosure as _disclosure
from app.shared import error_codes
from app.shared.audit_action import audit_action
from app.shared.error_envelope import raise_typed_http
from app.utils.logger import get_logger


from app.core.auth.roles import (  # noqa: F401 (public compat compatibility export
    VALID_ROLES,
    ROLES_ABOVE_VIEWER,
    ROLES_ADMIN_OR_ABOVE,
    ROLES_KIOSK_ONLY,
)

logger = get_logger(__name__)

# RATE LIMITING
# R-01: backend Redis-backed cluster-safe con fallback in-memory
# La logica Redis vive in `app.shared.login_rate_limiter`; qui restano
# i wrapper che il login chiama. Lo store + algoritmo del fallback
# in-memory vivono in `app.core.auth.rate_limit_fallback`
# Le costanti LOGIN_MAX_ATTEMPTS / LOGIN_WINDOW_SECONDS / LOGIN_LOCKOUT_SECONDS
# restano modulo-level e vengono lette al volo dai wrapper, cosi' i test
# possono ancora monkeypatcharle (vedi tests2/security/unit/test_login_rate_limit_per_ip.py
# KEEP_COMPAT: gli alias `_rate_lock`, `_failed_attempts`
# `_check_rate_limit_in_memory`, `_record_failed_login_in_memory`
# `_clear_failed_logins_in_memory` restano qui come thin pointer al
# modulo estratto. Test esistenti che fanno
# `auth_mod._failed_attempts.clear` / `with auth_mod._rate_lock:` /
# `monkeypatch.setattr(auth_mod, "_check_rate_limit_in_memory", ...)`
# continuano a funzionare invariati

# `InMemoryRateLimitFallback` compatibility export removed (proven dead — zero
# callsites in `backend/app`, `backend/workers`, `backend/tests2`
# verified by exhaustive grep + AST scan). The class remains the
# canonical implementation in `app.core.auth.rate_limit_fallback`
# importers should pull it from there if they ever need the type
from app.core.auth.rate_limit_fallback import (
    RateLimitFallbackTriggered,
    _STORE as _rate_limit_fallback_store,
    record_failed_login_in_memory as _record_failed_login_in_memory,
    clear_failed_logins_in_memory as _clear_failed_logins_in_memory,
)

_rate_lock = _rate_limit_fallback_store.lock
_failed_attempts = _rate_limit_fallback_store.failed_attempts

# BE-CONFIG-001C: default tipati in `Settings.DEFAULTS` (10 / 60 / 300
# Override env continua a funzionare via `Settings._converti_tipo_env`
# coercion `int`). Le costanti restano modulo-level perche' i test
# `tests2/security/unit/test_login_rate_limit_per_ip.py` le
# monkeypatch-ano direttamente
LOGIN_MAX_ATTEMPTS    = int(_settings.get("LOGIN_MAX_ATTEMPTS"))
LOGIN_WINDOW_SECONDS  = int(_settings.get("LOGIN_WINDOW_SECONDS"))
LOGIN_LOCKOUT_SECONDS = int(_settings.get("LOGIN_LOCKOUT_SECONDS"))


def _check_rate_limit_in_memory(ip: str):
    """KEEP_COMPAT wrapper: legge le tre costanti monkeypatch-ate a
    livello di `auth.py` e delega al fallback estratto. Cattura
    `RateLimitFallbackTriggered` e converte in disclosure
    (`disclosure.rate_limit_exceeded(retry_after)`) preservando byte
    per byte il comportamento legacy (incl. clamp >=1 al
    Retry-After, che vive in `disclosure.rate_limit_exceeded`).
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
    from app.shared import login_rate_limiter as _rl
    _rl.check_rate_limit(
        ip,
        max_attempts=LOGIN_MAX_ATTEMPTS,
        window_seconds=LOGIN_WINDOW_SECONDS,
        lockout_seconds=LOGIN_LOCKOUT_SECONDS,
        fallback=_check_rate_limit_in_memory,
    )


def _record_failed_login(ip: str):
    from app.shared import login_rate_limiter as _rl
    _rl.record_failed_login(
        ip,
        window_seconds=LOGIN_WINDOW_SECONDS,
        fallback=_record_failed_login_in_memory,
    )


def _clear_failed_logins(ip: str):
    from app.shared import login_rate_limiter as _rl
    _rl.clear_failed_logins(
        ip,
        fallback=_clear_failed_logins_in_memory,
    )


# CONFIGURAZIONE JWT + UTILS
# STRUCT-005 Seam D: la logica vive in `app.shared.jwt_token`
# Qui restano i compatibility export per backward-compat con i test
# `auth_mod.SECRET_KEY`, `auth_mod.create_access_token`, ...) e con
# i consumer legacy che importano ancora JWT helpers dalla compatibility module
# Payload contract invariato: sub/username/group_id/role/exp/iat/jti
# Algoritmo: HS256. Policy SECRET_KEY invariata (RuntimeError prod/staging
# random dev). Nessun cambio comportamentale

from app.shared.jwt_token import (  # noqa: F401 (compatibility export pubblico)
    ALGORITHM,
    ACCESS_TOKEN_EXPIRE_HOURS,
    SECRET_KEY,
    create_access_token,
    decode_token,
)


from app.core.auth.models import (  # noqa: F401 (compatibility export pubblico)
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


from app.shared.password_hashing import (  # noqa: F401 (compatibility export pubblico)
    _ph,
    hash_password,
    verify_password,
    needs_rehash,
    _DUMMY_PASSWORD_HASH,
)

# DEPENDENCIES FASTAPI
#
# Compatibility exports for legacy callers. New code should import
# FastAPI auth dependencies from `app.core.auth.dependencies` directly.

from app.core.auth.dependencies import (  # noqa: F401 (compatibility export pubblico)
    _bearer_scheme,
    get_current_user,
    require_admin,
    require_not_esecutore,
    require_superadmin,
    require_user_or_above,
)

# Owner dependencies remain available here for compatibility. New code
# should import them from `app.core.auth.owner_dependencies` directly.

from app.core.auth.owner_dependencies import (  # noqa: F401 (compatibility export KEEP_COMPAT)
    require_file_owner,
    require_project_owner,
    require_schedule_owner,
)

# ROUTER AUTH

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
@audit_action(action="auth.login")
def login(data: LoginRequest, request: Request):
    """Login con username/password → JWT token."""
    from app.shared.request_utils import get_client_ip
    client_ip = get_client_ip(request)
    _check_rate_limit(client_ip)

    user = db.get_user_by_username(data.username)
    # C10: timing parity — verify_password SEMPRE, con dummy_hash se user
    # non esiste. Evita short-circuit che permetterebbe a un attaccante
    # di enumerare gli username misurando il tempo di risposta (bcrypt
    # richiede ~200-400ms; la differenza con la risposta 401 immediata
    # rendeva banale l'enumeration
    password_hash = user["password_hash"] if user else _DUMMY_PASSWORD_HASH
    password_ok = verify_password(data.password, password_hash)
    if not user or not password_ok:
        _record_failed_login(client_ip)
        _disclosure.raise_disclosure(_disclosure.invalid_credentials())
    if not user.get("is_active"):
        _disclosure.raise_disclosure(_disclosure.account_disabled())

    # MFA opt-in: se l'utente ha MFA attiva, il codice TOTP e' richiesto
    # Se MFA non e' attiva, il comportamento e' invariato (nessun impatto
    # Trade-off noto (vedi mfa.py): 403 mfa_required dopo password OK
    # differisce da 401 password errata, quindi rivela "utente con MFA
    # attiva". Mitigazione: rate limit su client_ip (gia' attivo
    from app.core.auth import mfa as _mfa
    if _mfa.is_enabled(user["id"]):
        if not data.mfa_code:
            _disclosure.raise_disclosure(_disclosure.mfa_required())
        if not _mfa.verify_code(user["id"], data.mfa_code):
            _record_failed_login(client_ip)
            _disclosure.raise_disclosure(_disclosure.mfa_invalid())

    _clear_failed_logins(client_ip)

    # Rehash progressivo bcrypt → Argon2id (best-effort: un fallimento del DB
    # non deve negare il login, al prossimo login riproveremo
    if needs_rehash(user["password_hash"]):
        try:
            db.update_user_password(user["id"], hash_password(data.password))
        except Exception as e:
            logger.warning(
                f"Rehash password fallito per {user['username']}: {e}",
                op="login_rehash",
            )

    token = create_access_token(
        user_id=user["id"], username=user["username"],
        group_id=user["group_id"], role=user["role"],
    )
    logger.info(f"Login: {user['username']} (gruppo: {user['group_id']})", op="login")
    return TokenResponse(
        access_token=token, user_id=user["id"], username=user["username"],
        group_id=user["group_id"], role=user["role"],
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
        # Token gia' scaduto: nessuna revoca serve (decode_token l'avrebbe
        # gia' rifiutato, ma tecnicamente potrebbe accadere in race
        return {"ok": True, "blacklisted": False}

    from app.shared.token_blacklist import blacklist_jti
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
    return UserResponse(**{k: user[k] for k in UserResponse.model_fields if k in user})


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

    if not verify_password(data.old_password, user["password_hash"]):
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
