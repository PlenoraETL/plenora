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
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import (
    VerifyMismatchError,
    VerificationError,
    InvalidHashError,
    HashingError,
)
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

# Import esplicito di app.config per side effect: garantisce che backend/.env
# sia stato caricato via python-dotenv PRIMA che leggiamo JWT_SECRET_KEY a
# livello di modulo. Senza questo, se auth.py venisse importato prima di
# app.config, la lettura di os.getenv("JWT_SECRET_KEY") sotto sarebbe fatta
# su un environment in cui il .env non è ancora stato applicato.
import app.config  # noqa: F401
import app.db.db_service as db
import app.db.config_db_service as cfg_db
from app.shared.audit_action import audit_action
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

LOGIN_MAX_ATTEMPTS    = int(os.getenv("LOGIN_MAX_ATTEMPTS", "10"))
LOGIN_WINDOW_SECONDS  = int(os.getenv("LOGIN_WINDOW_SECONDS", "60"))
LOGIN_LOCKOUT_SECONDS = int(os.getenv("LOGIN_LOCKOUT_SECONDS", "300"))


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
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Troppi tentativi. Riprova tra {max(retry_after, 1)} secondi.",
                headers={"Retry-After": str(max(retry_after, 1))},
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
# CONFIGURAZIONE JWT
# ============================================================
#
# Il SECRET_KEY usato per firmare i token JWT viene letto da
# JWT_SECRET_KEY nelle env var (caricate da backend/.env via app.config).
#
# Comportamento se la variabile manca:
#   - in produzione/staging  → l'app si rifiuta di partire (RuntimeError)
#   - in sviluppo            → viene generato un secret random per la sessione
#                              (i token diventano invalidi dopo il riavvio)
#
# NON c'è fallback hardcoded. Se vedi un riferimento a una "default JWT key"
# nelle vecchie versioni del codice, è stato rimosso intenzionalmente: una
# chiave hardcoded committata su GitHub vanifica completamente l'auth.

ALGORITHM  = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "8"))

_jwt_env_value = os.getenv("JWT_SECRET_KEY", "").strip()

if _jwt_env_value:
    SECRET_KEY = _jwt_env_value
else:
    _env = os.getenv("ENVIRONMENT", os.getenv("ENV", "development")).lower()
    if _env in ("production", "prod", "staging"):
        # RuntimeError invece di SystemExit: catturabile dai test, gestibile
        # dal framework FastAPI/uvicorn invece di kill brutale del processo.
        raise RuntimeError(
            "[PLENORA-ETL] JWT_SECRET_KEY non configurata in ambiente "
            f"'{_env}'. Imposta la variabile d'ambiente JWT_SECRET_KEY "
            "prima di avviare l'app.\n"
            "  Per generare una chiave robusta:\n"
            "    python -c \"import secrets; print(secrets.token_urlsafe(48))\"\n"
            "  Poi mettila in backend/.env oppure nel secret manager del deploy."
        )
    else:
        # In sviluppo: genera un secret random per sessione. Più sicuro di un
        # default hardcoded perché non è committato e cambia ad ogni riavvio.
        import secrets
        SECRET_KEY = secrets.token_hex(32)
        import warnings
        warnings.warn(
            "[PLENORA-ETL] JWT_SECRET_KEY non configurata: generato secret random per questa sessione. "
            "I token emessi non saranno validi dopo il riavvio dell'app. "
            "Imposta JWT_SECRET_KEY in backend/.env per avere persistenza.",
            stacklevel=2,
        )

# ============================================================
# MODELLI
# ============================================================

class LoginRequest(BaseModel):
    username: str
    password: str
    # MFA opt-in: None per utenti senza MFA (comportamento invariato).
    # Per utenti con MFA attiva: richiesto, altrimenti 403 mfa_required.
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

# ============================================================
# PASSWORD UTILS
# ============================================================
#
# Dual hashing: Argon2id (default per i nuovi hash) + bcrypt (verifica legacy).
# Gli utenti con hash bcrypt vengono migrati in Argon2id best-effort al primo
# login riuscito (rehash progressivo). hash_password ritorna SEMPRE Argon2id.

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    if not isinstance(hashed, str) or not hashed:
        return False
    if hashed.startswith("$argon2"):
        try:
            return _ph.verify(hashed, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
        except Exception:
            # Difensivo: argon2-cffi ha sollevato eccezioni non documentate
            # su input corrotti in passato. Fail-closed, no leak.
            return False
    if hashed.startswith("$2"):
        try:
            return bcrypt.checkpw(password.encode(), hashed.encode())
        except (ValueError, TypeError):
            return False
    return False


def needs_rehash(hashed: str) -> bool:
    """True se l'hash va rigenerato in Argon2id al prossimo login riuscito.

    - bcrypt ($2*)            → True (migrazione)
    - Argon2id valido         → delega a PasswordHasher.check_needs_rehash
    - altro / corrotto        → False (niente rehash alla cieca, il verify
                                 avrà già ritornato False a monte)
    """
    if not isinstance(hashed, str) or not hashed:
        return False
    if hashed.startswith("$2"):
        return True
    if hashed.startswith("$argon2"):
        try:
            return _ph.check_needs_rehash(hashed)
        except InvalidHashError:
            return False
        except Exception:
            return False
    return False


# C10: hash dummy reale di una password casuale, generato una volta all'import.
# Usato nel login quando l'username non esiste, cosi' `verify_password` fa
# comunque il lavoro di hashing e il tempo di risposta non rivela se un utente
# esiste o no (timing attack / user enumeration). Dall'introduzione di
# Argon2id il dummy e' in Argon2id: coerente con i nuovi utenti. Gli utenti
# legacy bcrypt hanno un costo di verify leggermente diverso finche' non
# vengono migrati dal rehash progressivo.
_DUMMY_PASSWORD_HASH = _ph.hash("c10_dummy_password_for_timing_parity")

# ============================================================
# JWT UTILS
# ============================================================

def create_access_token(user_id: str, username: str, group_id: str, role: str) -> str:
    # jti: identificatore univoco del token, usato come chiave della
    # blacklist Redis per supportare revoca via /auth/logout. uuid4.hex
    # e' 32 char esadecimali → 128 bit di entropia, collision-free.
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub":      user_id,
        "username": username,
        "group_id": group_id,
        "role":     role,
        "exp":      expire,
        "iat":      datetime.now(timezone.utc),
        "jti":      uuid.uuid4().hex,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

# ============================================================
# DEPENDENCIES FASTAPI
# ============================================================

_bearer_scheme = HTTPBearer(auto_error=False)

def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> CurrentUser:
    """Inietta l'utente autenticato. 401 se token mancante o invalido."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token mancante",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalido o scaduto",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Blacklist check: se il jti e' stato revocato via /auth/logout,
    # rifiuta anche se la signature+exp sarebbero valide. Precede la
    # query DB (piu' economico di un SELECT users).
    # Backward compat: token pre-rollout jti non hanno la claim → None
    # → `is_jti_blacklisted(None)` False → passa, come prima.
    from app.shared.token_blacklist import is_jti_blacklisted
    if is_jti_blacklisted(payload.get("jti")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token revocato",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = db.get_user(payload["sub"])
    if not user or not user.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utente non trovato o disabilitato",
        )
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
        raise HTTPException(
            status_code=403,
            detail="Permesso negato: i viewer/esecutori non possono creare/modificare risorse",
        )
    return current_user

def require_not_esecutore(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """
    Esclude SOLO il ruolo esecutore. Usato sugli endpoint di lettura "generale"
    (list_projects, list_connections, list_files, list_schedules, ecc.) che
    devono restare accessibili a viewer/user/admin/superadmin ma non al
    ruolo kiosk esecutore (che vede SOLO gli eseguibili del proprio gruppo).
    """
    if current_user.role in ROLES_KIOSK_ONLY:
        raise HTTPException(
            status_code=403,
            detail="Permesso negato: questo endpoint non è accessibile al ruolo esecutore",
        )
    return current_user

def require_admin(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Richiede ruolo admin o superadmin."""
    if current_user.role not in ROLES_ADMIN_OR_ABOVE:
        raise HTTPException(status_code=403, detail="Permesso negato: richiesto ruolo admin")
    return current_user

def require_superadmin(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Richiede ruolo superadmin."""
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Permesso negato: richiesto ruolo superadmin")
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
# Per risorse "owned" (projects, schedules, files): solo l'utente che l'ha
# creata, oppure un admin/superadmin del gruppo, può modificarla/eliminarla.
#
# Pattern: tre dependency dedicate (più esplicite di una parametrica) che
# leggono il record dal config_db_service e applicano il check.
# Records con created_by NULL non dovrebbero esistere dopo la migration
# Phase 1, ma per safety li trattiamo come "owned by group" → solo admin+
# del gruppo può toccarli.

def _extract_group_and_id(resource):
    """Estrae (group_id, id) da un dict o un oggetto Pydantic."""
    if isinstance(resource, dict):
        return resource.get("group_id"), resource.get("id")
    return getattr(resource, "group_id", None), getattr(resource, "id", None)


def assert_group_access(
    resource: dict | object | None,
    current_user: CurrentUser,
    label: str = "Risorsa",
) -> None:
    """
    Helper centralizzato per il group ownership check (operazioni di SCRITTURA).

    Sostituisce il pattern duplicato:
        if current_user.role != "superadmin" and resource.group_id != current_user.group_id:
            raise HTTPException(403, "Accesso negato")

    - 404 se `resource` è None.
    - 403 se l'utente non è superadmin e il gruppo della risorsa non combacia.
    - Accetta sia dict che oggetti Pydantic.

    NB: NON considera gli share (allowed_groups). Per le operazioni di SOLA LETTURA
    su risorse shareable usa `assert_resource_visible` che invece include gli share.

    NB: NON controlla l'ownership puntuale (`created_by`). Per quello le 3 dependency
    `require_*_owner`.
    """
    if resource is None:
        raise HTTPException(status_code=404, detail=f"{label} non trovata")

    if current_user.role == "superadmin":
        return  # superadmin bypassa

    resource_group, _ = _extract_group_and_id(resource)
    if resource_group != current_user.group_id:
        raise HTTPException(status_code=403, detail="Accesso negato")


def compute_can_edit(
    resource: dict | object | None,
    current_user: CurrentUser,
) -> bool:
    """
    Calcola se l'utente corrente può modificare/eliminare la risorsa.
    Pensato per le list/get response: il frontend usa il flag per nascondere
    i bottoni edit/delete.

    Regole (stesse di `assert_can_modify`):
    - True per superadmin (sempre)
    - True per admin del gruppo proprietario, MA solo se la risorsa NON è
      stata creata da un superadmin (hierarchical ownership)
    - False per user/viewer/altri gruppi/risorse create da super
    """
    if resource is None:
        return False
    if current_user.role == "superadmin":
        return True
    if current_user.role != "admin":
        return False  # user/viewer non possono modificare le risorse condivise

    resource_group, _ = _extract_group_and_id(resource)
    if resource_group != current_user.group_id:
        return False

    # admin del gruppo: ok solo se NON creata da un superadmin
    if isinstance(resource, dict):
        created_by = resource.get("created_by")
    else:
        created_by = getattr(resource, "created_by", None)

    if created_by and created_by != current_user.user_id:
        creator = db.get_user(created_by)
        if creator and creator.get("role") == "superadmin":
            return False

    return True


def assert_can_modify(
    resource: dict | object | None,
    current_user: CurrentUser,
    label: str = "Risorsa",
) -> None:
    """
    Hierarchical ownership check (operazioni di SCRITTURA).

    Regole:
    - 404 se resource is None
    - 403 se non sei superadmin e non sei del gruppo proprietario
    - 403 se l'utente che ha creato la risorsa è SUPERADMIN e tu sei admin
      (un admin di gruppo non può modificare risorse create da un superadmin)
    - Altrimenti OK

    Esempio:
    - superadmin crea connessione X nel gruppo "milano"
    - admin di "milano" prova update X -> 403 ("creata da superadmin")
    - solo superadmin può modificare X
    """
    if resource is None:
        raise HTTPException(status_code=404, detail=f"{label} non trovata")

    if current_user.role == "superadmin":
        return  # superadmin bypassa sempre

    resource_group, _ = _extract_group_and_id(resource)
    if resource_group != current_user.group_id:
        raise HTTPException(status_code=403, detail="Accesso negato")

    # Hierarchical check: chi l'ha creata?
    if isinstance(resource, dict):
        created_by = resource.get("created_by")
    else:
        created_by = getattr(resource, "created_by", None)

    if created_by and created_by != current_user.user_id:
        # Lookup live del ruolo del creator
        creator = db.get_user(created_by)
        if creator and creator.get("role") == "superadmin":
            raise HTTPException(
                status_code=403,
                detail=f"{label} creata da un superadmin: solo un superadmin può modificarla",
            )

    # Owned dal proprio gruppo, NON creato da un superadmin → ok
    return


def assert_resource_visible(
    resource: dict | object | None,
    table_name: str,
    current_user: CurrentUser,
    label: str = "Risorsa",
) -> None:
    """
    Variante "soft" di `assert_group_access` per le operazioni di SOLA LETTURA
    su risorse shareable (connections, datasources, eseguibili, api_connections,
    arcgis_connections).

    Visibile = owned dal proprio gruppo OPPURE condiviso via *_shares (incluso
    magic share '*'). Una risorsa condivisa può essere LETTA / TESTATA /
    BROWSATA, ma non modificata né eliminata (per quello serve `assert_group_access`).

    - 404 se `resource` è None
    - bypassa per superadmin
    - 403 altrimenti se non è né owned né shared
    """
    if resource is None:
        raise HTTPException(status_code=404, detail=f"{label} non trovata")

    if current_user.role == "superadmin":
        return

    resource_group, resource_id = _extract_group_and_id(resource)

    # Owned dal proprio gruppo: ok subito (no DB query)
    if resource_group == current_user.group_id:
        return

    # Shared via *_shares?
    if resource_id:
        from app.shared.sharing import is_resource_visible, is_shareable
        if is_shareable(table_name) and is_resource_visible(
            table_name,
            resource_id,
            current_user.group_id,
            is_superadmin=False,
            resource_owner_group_id=resource_group,
        ):
            return

    raise HTTPException(status_code=403, detail="Accesso negato")


def _check_ownership(
    resource: dict | None,
    resource_label: str,
    current_user: CurrentUser,
) -> None:
    """
    Logica condivisa per le 3 dependency `require_*_owner`.

    - 404 se la risorsa non esiste.
    - 403 se l'utente non è del gruppo (a meno che superadmin).
    - 403 se l'utente non è owner E non è admin/superadmin.
    """
    if not resource:
        raise HTTPException(status_code=404, detail=f"{resource_label} non trovato")

    is_super = current_user.role == "superadmin"
    is_admin_plus = current_user.role in ROLES_ADMIN_OR_ABOVE

    # Filtro gruppo (superadmin bypassa)
    if not is_super and resource.get("group_id") != current_user.group_id:
        raise HTTPException(status_code=403, detail="Accesso negato")

    # Owner check: created_by deve combaciare oppure admin/superadmin
    created_by = resource.get("created_by")
    if is_admin_plus:
        return  # admin del gruppo (o superadmin) può sempre
    if created_by and created_by == current_user.user_id:
        return  # sei tu l'owner

    raise HTTPException(
        status_code=403,
        detail=f"Permesso negato: solo l'owner del {resource_label.lower()} o un admin può eseguire questa operazione",
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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Username o password errati")
    if not user.get("is_active"):
        raise HTTPException(status_code=403, detail="Account disabilitato")

    # MFA opt-in: se l'utente ha MFA attiva, il codice TOTP e' richiesto.
    # Se MFA non e' attiva, il comportamento e' invariato (nessun impatto).
    # Trade-off noto (vedi mfa.py): 403 mfa_required dopo password OK
    # differisce da 401 password errata, quindi rivela "utente con MFA
    # attiva". Mitigazione: rate limit su client_ip (gia' attivo).
    from app.core.auth import mfa as _mfa
    if _mfa.is_enabled(user["id"]):
        if not data.mfa_code:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="mfa_required",
            )
        if not _mfa.verify_code(user["id"], data.mfa_code):
            _record_failed_login(client_ip)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="mfa_invalid",
            )

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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token mancante",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalido o scaduto",
            headers={"WWW-Authenticate": "Bearer"},
        )
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
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servizio di revoca token non disponibile, riprova",
        )
    logger.info(f"Logout: jti blacklistato, ttl={ttl}s", op="logout")
    return {"ok": True, "blacklisted": True, "ttl": ttl}


# --- Profilo personale ---

@router.get("/me", response_model=UserResponse)
def get_me(current_user: CurrentUser = Depends(get_current_user)):
    """Profilo dell'utente corrente. Qualsiasi autenticato."""
    user = db.get_user(current_user.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Utente non trovato")
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
        raise HTTPException(status_code=404, detail="Utente non trovato")

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
        raise HTTPException(status_code=400, detail="Codice MFA non valido")
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
        raise HTTPException(status_code=400, detail="Codice MFA non valido")
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
            raise HTTPException(
                status_code=403,
                detail="Solo un superadmin può creare utenti admin o superadmin",
            )

    if len(data.password) < 12:
        raise HTTPException(status_code=400, detail="La password deve essere lunga almeno 12 caratteri")

    existing = db.get_user_by_username(data.username)
    if existing:
        raise HTTPException(status_code=409, detail="Username già esistente")

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
        raise HTTPException(status_code=404, detail="Utente non trovato")
    if current_user.role != "superadmin" and target.get("group_id") != current_user.group_id:
        raise HTTPException(status_code=403, detail="Accesso negato")

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
        raise HTTPException(
            status_code=403,
            detail="Solo superadmin puo' modificare group_id",
        )

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
            raise HTTPException(status_code=403, detail="Non puoi modificare il tuo stesso ruolo")
        if "is_active" in fields and fields["is_active"] is False:
            raise HTTPException(status_code=403, detail="Non puoi disabilitare il tuo stesso account")

    updated = db.update_user(user_id, **fields)
    if not updated:
        raise HTTPException(status_code=404, detail="Utente non trovato")
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
        if current_user.role == "superadmin":
            raise HTTPException(status_code=404, detail="Utente non trovato")
        raise HTTPException(status_code=403, detail="Accesso negato")

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
        raise HTTPException(status_code=400, detail="Non puoi eliminare te stesso")

    target = db.get_user(user_id)
    # C10: stesso pattern di reset-password — 403 uniforme per admin
    # non-superadmin, evita enumeration.
    if not target or (
        current_user.role != "superadmin"
        and target.get("group_id") != current_user.group_id
    ):
        if current_user.role == "superadmin":
            raise HTTPException(status_code=404, detail="Utente non trovato")
        raise HTTPException(status_code=403, detail="Accesso negato")

    if not db.delete_user(user_id):
        raise HTTPException(status_code=404, detail="Utente non trovato")

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
        raise HTTPException(status_code=409, detail="group_id già esistente")
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
        raise HTTPException(status_code=404, detail="Gruppo non trovato")
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
        raise HTTPException(status_code=400, detail="Il gruppo 'default' non può essere eliminato")
    if not cfg_db.get_group(group_id):
        raise HTTPException(status_code=404, detail="Gruppo non trovato")

    try:
        report = cfg_db.delete_group_cascade(group_id)
    except Exception as e:
        logger.error(f"[CRASH] delete_group_cascade fallito per group_id={group_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno del server")

    return {"deleted": True, "group_id": group_id, "report": report}

