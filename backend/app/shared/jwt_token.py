"""
JWT access-token contract - emit/decode + env-loading policy

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

from __future__ import annotations

# Side-effect: caricamento di backend/.env via python-dotenv. Importato
# prima di leggere JWT_SECRET_KEY/JWT_EXPIRE_HOURS qui sotto
import app.config  # noqa: F401

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt


ALGORITHM = "HS256"
# BE-CONFIG-001B: TTL token migrato a `Settings.DEFAULTS` (default 8h
# Override env `JWT_EXPIRE_HOURS` continua a funzionare via type coercion
# `int` in `Settings._converti_tipo_env`. Lettura a import-time: la
# costante e' parte del contratto modulo (consumata da `auth.auth` e
# da test-contract `test_jwt_token_module_contract`). `app.config` e'
# gia' importato sopra dalla catena `app/main.py` -> `app/config.py`
# dotenv loaded), quindi `settings.get` qui legge gli override env
from app.config import settings as _settings
ACCESS_TOKEN_EXPIRE_HOURS = int(_settings.get("JWT_EXPIRE_HOURS"))

_jwt_env_value = os.getenv("JWT_SECRET_KEY", "").strip()

if _jwt_env_value:
    SECRET_KEY = _jwt_env_value
else:
    _env = os.getenv("ENVIRONMENT", os.getenv("ENV", "development")).lower()
    if _env in ("production", "prod", "staging"):
        # RuntimeError invece di SystemExit: catturabile dai test, gestibile
        # dal framework FastAPI/uvicorn invece di kill brutale del processo
        raise RuntimeError(
            "[PLENORA-ETL] JWT_SECRET_KEY non configurata in ambiente "
            f"'{_env}'. Imposta la variabile d'ambiente JWT_SECRET_KEY "
            "prima di avviare l'app.\n"
            "  Per generare una chiave robusta:\n"
            "    python -c \"import secrets; print(secrets.token_urlsafe(48))\"\n"
            "  Poi mettila in backend/.env oppure nel secret manager del deploy."
        )
    else:
        # In sviluppo: genera un secret random per sessione. Piu' sicuro di un
        # default hardcoded perche' non e' committato e cambia ad ogni riavvio
        import secrets
        SECRET_KEY = secrets.token_hex(32)
        import warnings
        warnings.warn(
            "[PLENORA-ETL] JWT_SECRET_KEY non configurata: generato secret random per questa sessione. "
            "I token emessi non saranno validi dopo il riavvio dell'app. "
            "Imposta JWT_SECRET_KEY in backend/.env per avere persistenza.",
            stacklevel=2,
        )


def create_access_token(user_id: str, username: str, group_id: str, role: str) -> str:
    # jti: identificatore univoco del token, usato come chiave della
    # blacklist Redis per supportare revoca via /auth/logout. uuid4.hex
    # e' 32 char esadecimali -> 128 bit di entropia, collision-free
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
