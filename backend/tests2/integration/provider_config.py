"""Non-secret provider configuration for integration tests."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


_CONFIG_DIR = Path(__file__).resolve().parent
_DEFAULT_CONFIG = _CONFIG_DIR / "provider_config.toml"
_LOCAL_CONFIG = _CONFIG_DIR / "provider_config.local.toml"


@dataclass(frozen=True)
class ProviderConfig:
    enabled: bool
    host: str
    port: int
    database: str
    username: str
    password: str
    schema: str | None = None
    service: str | None = None
    driver: str | None = None
    fallback_driver: str | None = None


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _all_config() -> dict[str, Any]:
    return _merge(_read_toml(_DEFAULT_CONFIG), _read_toml(_LOCAL_CONFIG))


def provider_config(name: str, *, default_password: str) -> ProviderConfig:
    raw = dict(_all_config().get(name, {}))
    password_env = str(raw.get("password_env") or "").strip()
    password = os.getenv(password_env, default_password) if password_env else default_password
    return ProviderConfig(
        enabled=bool(raw.get("enabled", False)),
        host=str(raw.get("host", "127.0.0.1")),
        port=int(raw.get("port", 0)),
        database=str(raw.get("database", "")),
        username=str(raw.get("username", "")),
        password=password,
        schema=raw.get("schema"),
        service=raw.get("service"),
        driver=raw.get("driver"),
        fallback_driver=raw.get("fallback_driver"),
    )


def require_provider_enabled(name: str, config: ProviderConfig) -> None:
    if not config.enabled:
        pytest.skip(
            f"{name} integration disabilitato in provider_config.toml; "
            "abilitalo in provider_config.local.toml per eseguirlo."
        )
