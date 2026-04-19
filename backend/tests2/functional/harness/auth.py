"""Bootstrap auth/DB per i test funzionali."""

from __future__ import annotations

import uuid

from app.core.auth.auth import hash_password
from app.db.config_db_service import create_group, create_user, delete_group_cascade

from tests2.functional.harness.http import http_json_response


def seed_functional_user(*, role: str = "admin") -> dict:
    """Crea un gruppo e un utente isolati per un test funzionale."""
    suffix = uuid.uuid4().hex[:8]
    group_id = f"func-grp-{suffix}"
    user_id = f"func-user-{suffix}"
    username = f"func_{suffix}"
    password = "FunctionalPass123!"

    create_group(group_id, f"Functional {suffix}", "tests2 functional")
    create_user(
        user_id=user_id,
        username=username,
        password_hash=hash_password(password),
        group_id=group_id,
        role=role,
        email=f"{username}@example.test",
    )
    return {
        "group_id": group_id,
        "user_id": user_id,
        "username": username,
        "password": password,
        "role": role,
    }


def cleanup_functional_user(seed: dict) -> None:
    """Rimuove tutto il perimetro creato per un utente funzionale."""
    delete_group_cascade(seed["group_id"])


def login_functional_user(base_url: str, *, username: str, password: str) -> dict:
    """Esegue login reale sul backend e ritorna il payload JWT."""
    status, body, _ = http_json_response(
        f"{base_url}/api/auth/login",
        method="POST",
        payload={"username": username, "password": password},
        timeout=10.0,
    )
    if status != 200:
        raise RuntimeError(f"Login functional fallito: HTTP {status} - {body}")
    return body
