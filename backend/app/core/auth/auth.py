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
# Set centralizzato dei ruoli validi.
# - viewer: sola lettura nel proprio gruppo, può lanciare esecuzioni
# - esecutore: ruolo "kiosk" — vede SOLO gli eseguibili del proprio gruppo,
#              nessun accesso al resto della UI. Privacy: cleanup post-download.

VALID_ROLES = ("esecutore", "viewer", "user", "admin", "superadmin")
ROLES_ABOVE_VIEWER = ("user", "admin", "superadmin")
ROLES_ADMIN_OR_ABOVE = ("admin", "superadmin")
# Ruoli che NON possono creare/modificare risorse generali ma possono
# eseguire eseguibili e (per viewer) leggere il resto. esecutore è il più
# limitato: non ha nemmeno accesso ai manager / liste.
ROLES_KIOSK_ONLY = ("esecutore",)

logger = get_logger(__name__)

# ============================================================
# RATE LIMITING
# ============================================================
#
# R-01: backend Redis-backed cluster-safe con fallback in-memory.
# La logica Redis vive in `app.shared.login_rate_limiter`; qui restano
# i wrapper che il login() chiama e lo store in-memory di fallback,
# mantenuto compatibile con i test esistenti che resettano
# `_failed_attempts` / `_rate_lock` direttamente.
#
# Le costanti LOGIN_MAX_ATTEMPTS / LOGIN_WINDOW_SECONDS / LOGIN_LOCKOUT_SECONDS
# restano modulo-level e vengono lette al volo dai wrapper, cosi' i test
# possono ancora monkeypatcharle (vedi tests2/security/unit/test_login_rate_limit_per_ip.py).

import threading
import time
from collections import defaultdict

_rate_lock = threading.Lock()
_failed_attempts: dict = defaultdict(list)

# BE-CONFIG-001C: default tipati in `Settings.DEFAULTS` (10 / 60 / 300).
# Override env continua a funzionare via `Settings._converti_tipo_env`
# (coercion `int`). Le costanti restano modulo-level perche' i test
# `tests2/security/unit/test_login_rate_limit_per_ip.py` le
# monkeypatch-ano direttamente.
LOGIN_MAX_ATTEMPTS    = int(_settings.get("LOGIN_MAX_ATTEMPTS"))
LOGIN_WINDOW_SECONDS  = int(_settings.get("LOGIN_WINDOW_SECONDS"))
LOGIN_LOCKOUT_SECONDS = int(_settings.get("LOGIN_LOCKOUT_SECONDS"))


def _check_rate_limit_in_memory(ip: str):
    """Implementazione in-memory (fallback se Redis non disponibile).

    Identica al pre-R-01: sliding window su `_failed_attempts[ip]`,
    Retry-After = lockout - (now - oldest_in_window), clamp >=1.
    """
    now = time.monotonic()
    with _rate_lock:
        _failed_attempts[ip] = [
            t for t in _failed_attempts[ip] if now - t < LOGIN_WINDOW_SECONDS
        ]
        if len(_failed_attempts[ip]) >= LOGIN_MAX_ATTEMPTS:
            oldest = _failed_attempts[ip][0]
            retry_after = int(LOGIN_LOCKOUT_SECONDS - (now - oldest))
            _disclosure.raise_disclosure(
                _disclosure.rate_limit_exceeded(retry_after)
            )


def _record_failed_login_in_memory(ip: str):
    with _rate_lock:
        _failed_attempts[ip].append(time.monotonic())


def _clear_failed_logins_in_memory(ip: str):
    with _rate_lock:
        _failed_attempts.pop(ip, None)


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
# `auth_middleware.py` che fa `from app.core.auth.auth import decode_token`.
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
    UserCreate,
    ChangePasswordRequest,
    UserResponse,
    ResetPasswordRequest,
    GroupCreate,
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

def group_filter(current_user: CurrentUser) -> Optional[str]:
    """
    Ritorna il group_id da usare come filtro.
    superadmin → None (nessun filtro, vede tutto)
    altri      → current_user.group_id
    """
    if current_user.role == "superadmin":
        return None
    return current_user.group_id


# ============================================================
# OWNERSHIP CHECKS
# ============================================================
#
# STRUCT-005 Seam B': i 6 helper puri vivono in `app.core.auth.ownership`.
# Qui restano i re-export per backward-compat con i 17 router che fanno
# `from app.core.auth.auth import assert_group_access, …`.
#
# Le 3 dependency FastAPI `require_*_owner` restano invece in questo
# modulo perche' usano `Depends(get_current_user)`: spostarle in
# ownership.py creerebbe un cycle `auth.py ↔ ownership.py`. Sono thin
# wrapper che chiamano `_check_ownership` (re-esportato qui sotto).

from app.core.auth.ownership import (  # noqa: F401  (re-export pubblico)
    _extract_group_and_id,
    assert_group_access,
    compute_can_edit,
    assert_can_modify,
    assert_resource_visible,
    _check_ownership,
)


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

@router.get("/users", response_model=list[UserResponse])
def list_users(current_user: CurrentUser = Depends(require_admin)):
    """
    Lista utenti.
    superadmin → tutti gli utenti di tutti i gruppi
    admin      → solo utenti del proprio gruppo
    """
    if current_user.role == "superadmin":
        # Superadmin: tutti gli utenti con una singola query (no N+1 per gruppo)
        import app.db.config_db_service as _cfg_db
        users = _cfg_db.fetchall("SELECT * FROM users ORDER BY username")
    else:
        users = db.list_users(current_user.group_id)
    return [UserResponse(**{k: u[k] for k in UserResponse.model_fields if k in u}) for u in users]

@router.post("/users", response_model=UserResponse, status_code=201)
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

@router.put("/users/{user_id}", response_model=UserResponse)
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

@router.post("/users/{user_id}/reset-password")
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

@router.delete("/users/{user_id}", status_code=204)
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

# ============================================================
# ROUTER GRUPPI
# ============================================================

groups_router = APIRouter(prefix="/auth/groups", tags=["groups"])

@groups_router.get("", response_model=list[GroupResponse])
def list_groups(current_user: CurrentUser = Depends(require_admin)):
    """
    Lista gruppi.
    superadmin → tutti i gruppi (cross-tenant)
    admin      → solo il proprio gruppo (l'admin non può fare sharing
                  cross-gruppo, quindi non ha bisogno di vedere gli altri)
    """
    if current_user.role == "superadmin":
        groups = db.list_groups()
    else:
        g = db.get_group(current_user.group_id)
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
    if db.get_group(data.group_id):
        _disclosure.raise_disclosure(_disclosure.group_id_already_exists())
    group = db.create_group(data.group_id, data.name, data.description)
    return GroupResponse(group_id=group["id"], name=group["name"], description=group.get("description", ""))

@groups_router.get("/{group_id}/summary")
def get_group_summary(group_id: str,
                      current_user: CurrentUser = Depends(require_superadmin)):
    """
    Ritorna il conteggio di tutte le entità del gruppo.
    Usato dal frontend per mostrare il modale di conferma prima del cascade delete.
    """
    import app.db.config_db_service as cfg_db
    if not cfg_db.get_group(group_id):
        _disclosure.raise_disclosure(_disclosure.group_not_found())
    return cfg_db.get_group_summary(group_id)


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
    import app.db.config_db_service as cfg_db
    if group_id == "default":
        _disclosure.raise_disclosure(_disclosure.group_default_not_deletable())
    if not cfg_db.get_group(group_id):
        _disclosure.raise_disclosure(_disclosure.group_not_found())

    try:
        report = cfg_db.delete_group_cascade(group_id)
    except Exception as e:
        logger.error(f"[CRASH] delete_group_cascade fallito per group_id={group_id}: {e}", exc_info=True)
        raise_typed_http(
            error_codes.INTERNAL_ERROR,
            status_code=500,
            message="Errore interno del server",
        )

    return {"deleted": True, "group_id": group_id, "report": report}

