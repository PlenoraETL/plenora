"""
URBY-ETL — Autenticazione JWT

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

import threading
import time
from collections import defaultdict

_rate_lock = threading.Lock()
_failed_attempts: dict = defaultdict(list)

LOGIN_MAX_ATTEMPTS    = int(os.getenv("LOGIN_MAX_ATTEMPTS", "10"))
LOGIN_WINDOW_SECONDS  = int(os.getenv("LOGIN_WINDOW_SECONDS", "60"))
LOGIN_LOCKOUT_SECONDS = int(os.getenv("LOGIN_LOCKOUT_SECONDS", "300"))


def _check_rate_limit(ip: str):
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


def _record_failed_login(ip: str):
    with _rate_lock:
        _failed_attempts[ip].append(time.monotonic())


def _clear_failed_logins(ip: str):
    with _rate_lock:
        _failed_attempts.pop(ip, None)


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
            "[URBY-ETL] JWT_SECRET_KEY non configurata in ambiente "
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
            "[URBY-ETL] JWT_SECRET_KEY non configurata: generato secret random per questa sessione. "
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

class GroupResponse(BaseModel):
    group_id: str
    name: str
    description: str = ""

# ============================================================
# PASSWORD UTILS
# ============================================================

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

# ============================================================
# JWT UTILS
# ============================================================

def create_access_token(user_id: str, username: str, group_id: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub":      user_id,
        "username": username,
        "group_id": group_id,
        "role":     role,
        "exp":      expire,
        "iat":      datetime.now(timezone.utc),
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
def login(data: LoginRequest, request: Request):
    """Login con username/password → JWT token."""
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    user = db.get_user_by_username(data.username)
    if not user or not verify_password(data.password, user["password_hash"]):
        _record_failed_login(client_ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Username o password errati")
    if not user.get("is_active"):
        raise HTTPException(status_code=403, detail="Account disabilitato")

    _clear_failed_logins(client_ip)
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

# --- Profilo personale ---

@router.get("/me", response_model=UserResponse)
def get_me(current_user: CurrentUser = Depends(get_current_user)):
    """Profilo dell'utente corrente. Qualsiasi autenticato."""
    user = db.get_user(current_user.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    return UserResponse(**{k: user[k] for k in UserResponse.model_fields if k in user})


@router.post("/change-password")
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

    if len(data.new_password) < 6:
        raise HTTPException(status_code=400, detail="La nuova password deve essere lunga almeno 6 caratteri")

    db.update_user_password(current_user.user_id, hash_password(data.new_password))
    logger.info(f"Password cambiata: {current_user.username}", op="change_password")
    return {"ok": True}


# --- Gestione utenti (admin only) ---

@router.get("/users", response_model=list[UserResponse])
def list_users(current_user: CurrentUser = Depends(require_admin)):
    """
    Lista utenti.
    superadmin → tutti gli utenti di tutti i gruppi
    admin      → solo utenti del proprio gruppo
    """
    if current_user.role == "superadmin":
        # Recupera utenti da tutti i gruppi
        seen = set()
        users = []
        groups = db.list_groups()
        # Aggiungi sempre il gruppo corrente per sicurezza
        group_ids = {g["id"] for g in groups} | {current_user.group_id}
        for gid in group_ids:
            for u in db.list_users(gid):
                if u["id"] not in seen:
                    seen.add(u["id"])
                    users.append(u)
    else:
        users = db.list_users(current_user.group_id)
    return [UserResponse(**{k: u[k] for k in UserResponse.model_fields if k in u}) for u in users]

@router.post("/users", response_model=UserResponse, status_code=201)
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
def reset_password(user_id: str, data: ResetPasswordRequest,
                   current_user: CurrentUser = Depends(require_admin)):
    """
    Reset password di un utente (senza richiedere la vecchia).
    superadmin → qualsiasi utente
    admin      → solo utenti del proprio gruppo
    """
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    if current_user.role != "superadmin" and target.get("group_id") != current_user.group_id:
        raise HTTPException(status_code=403, detail="Accesso negato")

    db.update_user_password(user_id, hash_password(data.new_password))
    return {"ok": True}

@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: str, current_user: CurrentUser = Depends(require_admin)):
    """
    Elimina utente.
    superadmin → qualsiasi utente
    admin      → solo nel proprio gruppo, non se stesso
    """
    if user_id == current_user.user_id:
        raise HTTPException(status_code=400, detail="Non puoi eliminare te stesso")

    target = db.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    if current_user.role != "superadmin" and target.get("group_id") != current_user.group_id:
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
    return [GroupResponse(group_id=g["id"], name=g["name"], description=g.get("description", ""))
            for g in groups if g]

@groups_router.post("", response_model=GroupResponse, status_code=201)
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
        raise HTTPException(status_code=500, detail=f"Errore durante l'eliminazione: {e}")

    return {"deleted": True, "group_id": group_id, "report": report}

