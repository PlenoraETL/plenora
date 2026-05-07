"""Non-secret provider configuration for integration tests."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    import tomli as tomllib  # type: ignore[no-redef]


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


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    raise ValueError(f"Invalid boolean value in provider_config.toml: {value!r}")


def _as_int(value: Any, *, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid integer value for {field} in provider_config.toml: {value!r}") from exc


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def provider_config(name: str, *, default_password: str) -> ProviderConfig:
    raw = dict(_all_config().get(name, {}))
    password_env = str(raw.get("password_env") or "").strip()
    password = os.getenv(password_env, default_password) if password_env else default_password
    return ProviderConfig(
        enabled=_as_bool(raw.get("enabled"), default=False),
        host=str(raw.get("host", "127.0.0.1")),
        port=_as_int(raw.get("port", 0), field=f"{name}.port"),
        database=str(raw.get("database", "")),
        username=str(raw.get("username", "")),
        password=password,
        schema=_as_optional_str(raw.get("schema")),
        service=_as_optional_str(raw.get("service")),
        driver=_as_optional_str(raw.get("driver")),
        fallback_driver=_as_optional_str(raw.get("fallback_driver")),
    )


def require_provider_enabled(name: str, config: ProviderConfig) -> None:
    if not config.enabled:
        pytest.skip(
            f"{name} integration disabilitato in provider_config.toml; "
            "abilitalo in provider_config.local.toml per eseguirlo."
        )
