"""Docker acceptance seed for the API smoke flow.

This module is intentionally explicit and opt-in: it is meant to be executed
inside the backend container by CI before `scripts/docker-api-smoke.sh`.
"""

from __future__ import annotations

import uuid

from app.core.auth import db
from app.environment import environment_name, get_env
from app.shared.password_hashing import hash_password


DEFAULT_GROUP_ID = "smoke"
DEFAULT_USERNAME = "smoke-user"


def _required_env(name: str, default: str | None = None) -> str:
    value = get_env(name, default)
    if value:
        return value
    raise SystemExit(f"{name} is required for Docker API smoke seeding")


def _assert_non_prod() -> None:
    env_name = environment_name()
    if env_name in {"production", "prod", "staging", "stage"}:
        raise SystemExit("Refusing to seed smoke credentials in production/staging")


def seed_smoke_identity() -> None:
    _assert_non_prod()

    username = _required_env("PLENORA_SMOKE_USERNAME", DEFAULT_USERNAME)
    password = _required_env("PLENORA_SMOKE_PASSWORD")
    group_id = _required_env("PLENORA_SMOKE_GROUP_ID", DEFAULT_GROUP_ID)

    if db.get_group(group_id) is None:
        db.create_group(group_id, "Smoke Test", "Docker API smoke group")

    password_hash = hash_password(password)
    existing = db.get_user_by_username(username)
    if existing is None:
        db.create_user(
            user_id=f"smoke-{uuid.uuid4()}",
            username=username,
            password_hash=password_hash,
            group_id=group_id,
            role="user",
            email="smoke@example.invalid",
        )
        return

    db.update_user(
        existing.id,
        email=existing.email or "smoke@example.invalid",
        is_active=True,
        role="user",
        group_id=group_id,
    )
    db.update_user_password(existing.id, password_hash)


def main() -> None:
    seed_smoke_identity()
    print("Docker API smoke identity seeded")


if __name__ == "__main__":
    main()
