"""Single boundary for direct process-environment reads.

Application services should depend on ``app.config.settings``. Bootstrap and
low-level infrastructure code that must inspect raw environment values should
do it through this module so ``os.getenv`` does not leak into domain code.
"""

from __future__ import annotations

import os


HARDENED_ENV_NAMES = frozenset({"production", "prod", "staging", "stage"})
TRUTHY_ENV_VALUES = frozenset({"true", "1", "yes", "on"})


def get_env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


def require_env(name: str, default: str | None = None) -> str:
    value = get_env(name, default)
    if value:
        return value
    raise RuntimeError(f"{name} is required")


def environment_name(default: str = "development") -> str:
    return (get_env("ENVIRONMENT") or get_env("ENV") or default).lower()


def is_hardened_environment() -> bool:
    return environment_name() in HARDENED_ENV_NAMES


def env_flag(name: str, default: bool = False) -> bool:
    value = get_env(name)
    if value is None:
        return default
    return value.strip().lower() in TRUTHY_ENV_VALUES
