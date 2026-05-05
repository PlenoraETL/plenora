"""
PLENORA-ETL — Autenticazione JWT

Modello RBAC:
  superadmin → tutto, senza filtro gruppo
  admin      → CRUD utenti viewer/user/esecutore del proprio gruppo, gestione risorse gruppo
  user       → CRUD risorse del proprio gruppo (sue + condivise),
               può lanciare esecuzioni, no gestione utenti
  viewer     → sola lettura risorse del proprio gruppo (sue + condivise),
               PUÒ lanciare esecuzioni, NON può creare/modificare/eliminare nulla
  esecutore  → ruolo "kiosk" totalmente isolato. Vede SOLO la lista degli
               eseguibili del proprio gruppo (+ shared cross-gruppo) e può
               lanciarli. NON ha accesso a editor, manager, scheduler, file,
               connessioni, log, ecc. Pensato per utenti finali non tecnici
               che consumano pipeline pre-confezionate. Le esecuzioni sono
               soggette a cleanup post-download (privacy / GDPR).

Ownership: alcune risorse (projects, schedules, files) hanno un campo `created_by`.
Solo l'owner o un admin/superadmin può modificarle/eliminarle. Vedi
`require_project_owner`, `require_schedule_owner`, `require_file_owner`.
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
# su un environment in cui il .env non è ancora stato applicato.
import app.config  # noqa: F401
from app.config import settings as _settings
import app.db.db_service as db
import app.db.config_db_service as cfg_db
from app.core.auth import disclosure as _disclosure
from app.shared import error_codes
from app.shared.audit_action import audit_action
from app.shared.error_envelope import raise_typed_http
from app.utils.logger import get_logger


# ============================================================
# RUOLI
# ============================================================
# BE-GO-READY-AUTH-ENGINE-003: role constants live in a small shared
# module so extracted auth routers can import them without importing
# this whole `auth.py` module and creating a circular import.
from app.core.auth.roles import (  # noqa: F401  (public compat re-export)
    VALID_ROLES,
    ROLES_ABOVE_VIEWER,
    ROLES_ADMIN_OR_ABOVE,
    ROLES_KIOSK_ONLY,
)

logger = get_logger(__name__)

# ============================================================
# RATE LIMITING
# ============================================================
#
# R-01: backend Redis-backed cluster-safe con fallback in-memory.
# La logica Redis vive in `app.shared.login_rate_limiter`; qui restano
# i wrapper che il login() chiama. Lo store + algoritmo del fallback
# in-memory vivono in `app.core.auth.rate_limit_fallback`
# (BE-GO-READY-AUTH-ENGINE-002-RATE-LIMIT-FALLBACK-EXTRACTION).
#
# Le costanti LOGIN_MAX_ATTEMPTS / LOGIN_WINDOW_SECONDS / LOGIN_LOCKOUT_SECONDS
# restano modulo-level e vengono lette al volo dai wrapper, cosi' i test
# possono ancora monkeypatcharle (vedi tests2/security/unit/test_login_rate_limit_per_ip.py).
#
# KEEP_COMPAT: gli alias `_rate_lock`, `_failed_attempts`,
# `_check_rate_limit_in_memory`, `_record_failed_login_in_memory`,
# `_clear_failed_logins_in_memory` restano qui come thin pointer al
# modulo estratto. Test esistenti che fanno
# `auth_mod._failed_attempts.clear()` / `with auth_mod._rate_lock:` /
# `monkeypatch.setattr(auth_mod, "_check_rate_limit_in_memory", ...)`
# continuano a funzionare invariati.

# BE-GO-READY-AUTH-ENGINE-018-FACADE-DEATH-AUDIT-SAFE-DELETIONS:
# `InMemoryRateLimitFallback` re-export removed (proven dead — zero
# callsites in `backend/app`, `backend/workers`, `backend/tests2`
# verified by exhaustive grep + AST scan). The class remains the
# canonical implementation in `app.core.auth.rate_limit_fallback`;
# importers should pull it from there if they ever need the type.
from app.core.auth.rate_limit_fallback import (
    RateLimitFallbackTriggered,
    _STORE as _rate_limit_fallback_store,
    record_failed_login_in_memory as _record_failed_login_in_memory,
    clear_failed_logins_in_memory as _clear_failed_logins_in_memory,
)

# KEEP_COMPAT: alias dei membri dello store estratto. Identita' Python
# preservata (`auth_mod._rate_lock is rate_limit_fallback._STORE.lock`)
# cosi' un `with auth_mod._rate_lock:` nel test e un `self.lock` nel
# nuovo modulo serializzano sulla stessa primitiva di sincronia. Idem
# per `_failed_attempts`: l'oggetto `defaultdict(list)` e' lo stesso,
# quindi `auth_mod._failed_attempts.clear()` svuota lo store reale.
_rate_lock = _rate_limit_fallback_store.lock
_failed_attempts = _rate_limit_fallback_store.failed_attempts

# BE-CONFIG-001C: default tipati in `Settings.DEFAULTS` (10 / 60 / 300).
# Override env continua a funzionare via `Settings._converti_tipo_env`
# (coercion `int`). Le costanti restano modulo-level perche' i test
# `tests2/security/unit/test_login_rate_limit_per_ip.py` le
# monkeypatch-ano direttamente.
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


# ============================================================
# CONFIGURAZIONE JWT + UTILS
# ============================================================
#
# STRUCT-005 Seam D: la logica vive in `app.shared.jwt_token`.
# Qui restano i re-export per backward-compat con i test
# (`auth_mod.SECRET_KEY`, `auth_mod.create_access_token`, ...) e con
# i consumer legacy che importano ancora JWT helpers dalla facade.
#
# Payload contract invariato: sub/username/group_id/role/exp/iat/jti.
# Algoritmo: HS256. Policy SECRET_KEY invariata (RuntimeError prod/staging,
# random dev). Nessun cambio comportamentale.

from app.shared.jwt_token import (  # noqa: F401  (re-export pubblico)
    ALGORITHM,
    ACCESS_TOKEN_EXPIRE_HOURS,
    SECRET_KEY,
    create_access_token,
    decode_token,
)

# ============================================================
# MODELLI
# ============================================================
#
# STRUCT-005 Seam C: i Pydantic DTO vivono in `app.core.auth.models`.
# Qui restano i re-export per backward-compat con i test e i ~20 router
# che fanno `from app.core.auth.auth import CurrentUser, ...`.

from app.core.auth.models import (  # noqa: F401  (re-export pubblico)
    LoginRequest,
    TokenResponse,
    CurrentUser,
    # BE-GO-READY-AUTH-ENGINE-021-USERS-GROUPS-REEXPORT-REMOVAL:
    # `UserCreate`, `ResetPasswordRequest`, `GroupCreate` rimossi
    # dal facade. Erano 1-occurrence-only in auth.py (zero uso
    # interno); zero callsite via `from app.core.auth.auth import`
    # in tutto l'albero (verificato whole-tree). Consumer canonici:
    # `app.core.auth.users_router` (interno),
    # `app.core.auth.groups_router` (interno),
    # `app.shared.contracts.auth` (Phase-10 fan-out per tests +
    # workers + futuri esterni).
    ChangePasswordRequest,
    UserResponse,
    GroupResponse,
    MFAEnrollResponse,
    MFAVerifyRequest,
    MFADisableRequest,
    MFAStatusResponse,
)

# ============================================================
# PASSWORD UTILS
# ============================================================
#
# STRUCT-005 Seam A: la logica vive in `app.shared.password_hashing`.
# Qui restano i re-export per backward-compat dei test e dei moduli
# esterni che fanno `from app.core.auth.auth import hash_password, ...`.
#
# Nessun cambio comportamentale: i simboli importati sono gli stessi
# oggetti del modulo nuovo (identita' preservata per monkeypatch).

from app.shared.password_hashing import (  # noqa: F401  (re-export pubblico)
    _ph,
    hash_password,
    verify_password,
    needs_rehash,
    _DUMMY_PASSWORD_HASH,
)

# ============================================================
# DEPENDENCIES FASTAPI
# ============================================================

_bearer_scheme = HTTPBearer(auto_error=False)

def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> CurrentUser:
    """Inietta l'utente autenticato. 401 se token mancante o invalido."""
    if credentials is None:
        _disclosure.raise_disclosure(_disclosure.token_missing())
    payload = decode_token(credentials.credentials)
    if payload is None:
        _disclosure.raise_disclosure(_disclosure.token_invalid())
    # Blacklist check: se il jti e' stato revocato via /auth/logout,
    # rifiuta anche se la signature+exp sarebbero valide. Precede la
    # query DB (piu' economico di un SELECT users).
    # Backward compat: token pre-rollout jti non hanno la claim → None
    # → `is_jti_blacklisted(None)` False → passa, come prima.
    from app.shared.token_blacklist import is_jti_blacklisted
    if is_jti_blacklisted(payload.get("jti")):
        _disclosure.raise_disclosure(_disclosure.token_revoked())
    user = db.get_user(payload["sub"])
    if not user or not user.get("is_active"):
        _disclosure.raise_disclosure(_disclosure.user_not_found_or_disabled())
    return CurrentUser(
        user_id=payload["sub"],
        username=payload["username"],
        group_id=payload["group_id"],
        role=payload["role"],
    )

def require_user_or_above(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """
    Richiede ruolo user, admin o superadmin (esclude viewer ed esecutore).
    Da usare per endpoint di creazione/modifica risorse: viewer è sola lettura,
    esecutore non vede nemmeno la UI di gestione risorse.
    """
    if current_user.role not in ROLES_ABOVE_VIEWER:
        _disclosure.raise_disclosure(_disclosure.role_insufficient_for_write())
    return current_user

def require_not_esecutore(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """
    Esclude SOLO il ruolo esecutore. Usato sugli endpoint di lettura "generale"
    (list_projects, list_connections, list_files, list_schedules, ecc.) che
    devono restare accessibili a viewer/user/admin/superadmin ma non al
    ruolo kiosk esecutore (che vede SOLO gli eseguibili del proprio gruppo).
    """
    if current_user.role in ROLES_KIOSK_ONLY:
        _disclosure.raise_disclosure(_disclosure.role_kiosk_only())
    return current_user

def require_admin(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Richiede ruolo admin o superadmin."""
    if current_user.role not in ROLES_ADMIN_OR_ABOVE:
        _disclosure.raise_disclosure(_disclosure.role_admin_required())
    return current_user

def require_superadmin(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Richiede ruolo superadmin."""
    if current_user.role != "superadmin":
        _disclosure.raise_disclosure(_disclosure.role_superadmin_required())
    return current_user

# BE-GO-READY-AUTH-ENGINE-011-GROUP-FILTER-LEAF-EXTRACTION:
# `group_filter` canonical home is now `app.core.auth.ownership`
# (pure policy helper, zero side effects). Re-export preserved as
# `KEEP_COMPAT` so any test/code that still does
# `from app.core.auth.auth import group_filter` (or
# `auth_mod.group_filter`) continues to resolve to the same object.
# App routers (9) consume the leaf directly per Phase-6 strangler
# ban (`tests2/security/unit/test_auth_facade_strangler_contract.py`).
from app.core.auth.ownership import group_filter  # noqa: F401


# ============================================================
# OWNERSHIP CHECKS
# ============================================================
#
# STRUCT-005 Seam B': i 6 helper puri vivono in `app.core.auth.ownership`.
#
# BE-GO-READY-AUTH-ENGINE-019: 4 re-export rimossi
# (`_extract_group_and_id`, `assert_group_access`,
# `compute_can_edit`, `assert_can_modify`). Tutti i 6 router
# production (files / arricchisci / features / connections /
# datasources / eseguibili) importano dal leaf.
#
# BE-GO-READY-AUTH-ENGINE-020: chiusura R-019.1 — IMDF migrato
# al leaf (`app/core/imdf/router.py` ora ha import top-level
# `from app.core.auth.ownership import assert_resource_visible`)
# e i 2 test IMDF (`test_imdf_security_contract.py`,
# `test_imdf_bundles_management_contract.py`) monkeypatchano via
# `app.core.imdf.router.assert_resource_visible`. Il re-export
# `assert_resource_visible` dal facade e' rimosso.
#
# Resta UN solo re-export, esplicitamente interno:
#
#   - `_check_ownership`: usato INTERNAMENTE dai 3 wrapper
#     `require_*_owner` qui sotto. Non e' superficie pubblica, ma
#     l'import deve esserci perche' i wrapper lo chiamano.
#
# Le 3 dependency FastAPI `require_*_owner` restano in questo
# modulo perche' usano `Depends(get_current_user)`: spostarle in
# ownership.py creerebbe un cycle `auth.py ↔ ownership.py`. Sono
# thin wrapper che chiamano `_check_ownership`.

# Internal-only: usato dai 3 require_*_owner sotto.
from app.core.auth.ownership import _check_ownership  # noqa: F401


def require_project_owner(
    project_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Owner check su projects. Da usare per update/delete/canvas/duplicate."""
    project = cfg_db.get_project(project_id)
    _check_ownership(project, "Progetto", current_user)
    return current_user


def require_schedule_owner(
    schedule_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Owner check su schedules. Da usare per update/delete/toggle/run."""
    schedule = cfg_db.get_schedule(schedule_id)
    _check_ownership(schedule, "Schedule", current_user)
    return current_user


def require_file_owner(
    file_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Owner check su file_sources. Da usare per delete."""
    file_source = cfg_db.get_file_source(file_id)
    _check_ownership(file_source, "File", current_user)
    return current_user

# ============================================================
# ROUTER AUTH
# ============================================================

router = APIRouter(prefix="/auth", tags=["auth"])

# --- Login ---

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
    # rendeva banale l'enumeration).
    password_hash = user["password_hash"] if user else _DUMMY_PASSWORD_HASH
    password_ok = verify_password(data.password, password_hash)
    if not user or not password_ok:
        _record_failed_login(client_ip)
        _disclosure.raise_disclosure(_disclosure.invalid_credentials())
    if not user.get("is_active"):
        _disclosure.raise_disclosure(_disclosure.account_disabled())

    # MFA opt-in: se l'utente ha MFA attiva, il codice TOTP e' richiesto.
    # Se MFA non e' attiva, il comportamento e' invariato (nessun impatto).
    # Trade-off noto (vedi mfa.py): 403 mfa_required dopo password OK
    # differisce da 401 password errata, quindi rivela "utente con MFA
    # attiva". Mitigazione: rate limit su client_ip (gia' attivo).
    from app.core.auth import mfa as _mfa
    if _mfa.is_enabled(user["id"]):
        if not data.mfa_code:
            _disclosure.raise_disclosure(_disclosure.mfa_required())
        if not _mfa.verify_code(user["id"], data.mfa_code):
            _record_failed_login(client_ip)
            _disclosure.raise_disclosure(_disclosure.mfa_invalid())

    _clear_failed_logins(client_ip)

    # Rehash progressivo bcrypt → Argon2id (best-effort: un fallimento del DB
    # non deve negare il login, al prossimo login riproveremo).
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

# --- Logout (revoca token via blacklist Redis) ---

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
        # gia' rifiutato, ma tecnicamente potrebbe accadere in race).
        return {"ok": True, "blacklisted": False}

    from app.shared.token_blacklist import blacklist_jti
    if not blacklist_jti(jti, ttl):
        _disclosure.raise_disclosure(_disclosure.revocation_unavailable())
    logger.info(f"Logout: jti blacklistato, ttl={ttl}s", op="logout")
    return {"ok": True, "blacklisted": True, "ttl": ttl}


# --- Profilo personale ---

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


# --- MFA TOTP (opt-in) ---

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


# --- Gestione utenti (admin only) ---
# BE-GO-READY-AUTH-ENGINE-003-USERS-ROUTER-EXTRACTION:
# i 5 endpoint admin CRUD `/auth/users*` vivono in
# `app.core.auth.users_router`. `users_router` resta re-esportato
# qui (KEEP_COMPAT) per non rompere `from app.core.auth.auth import
# users_router` nei caller / test esistenti. `main.py` lo monta
# direttamente dal modulo nuovo.
#
# BE-GO-READY-AUTH-ENGINE-021-USERS-GROUPS-REEXPORT-REMOVAL:
# le 5 funzioni handler (`list_users`, `create_user`, `update_user`,
# `reset_password`, `delete_user`) NON sono piu' re-esportate dal
# facade. Zero callsite produzione (verificato whole-tree); i 3 test
# che le chiamavano via `auth_mod.<name>` sono stati migrati a
# `app.core.auth.users_router` (canonical handler home).

from app.core.auth.users_router import users_router  # noqa: F401

# ============================================================
# ROUTER GRUPPI
# ============================================================
# BE-GO-READY-AUTH-ENGINE-004-GROUPS-ROUTER-EXTRACTION:
# i 4 endpoint admin CRUD `/auth/groups*` vivono in
# `app.core.auth.groups_router`. `groups_router` resta re-esportato
# qui (KEEP_COMPAT) per non rompere `from app.core.auth.auth import
# groups_router` nei caller esistenti (incluso `main.py` storico).
#
# BE-GO-READY-AUTH-ENGINE-021-USERS-GROUPS-REEXPORT-REMOVAL:
# le 4 funzioni handler (`list_groups`, `create_group`,
# `get_group_summary`, `delete_group`) NON sono piu' re-esportate
# dal facade. Zero callsite produzione (verificato whole-tree); i 2
# test che le toccavano (delete_group log + list_groups null safety)
# sono stati migrati a `app.core.auth.groups_router`.

from app.core.auth.groups_router import groups_router  # noqa: F401

