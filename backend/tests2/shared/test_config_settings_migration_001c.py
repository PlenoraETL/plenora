"""
Contract test BE-CONFIG-001C — login + redis-timeouts security policy.

Coperti:
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings, settings


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_APP = Path(__file__).resolve().parent.parent.parent / "app"


# Defaults present + tipati + valori storici

@pytest.mark.parametrize(
    "key, expected_type, expected_default",
    [
        ("LOGIN_MAX_ATTEMPTS", int, 10),
        ("LOGIN_WINDOW_SECONDS", int, 60),
        ("LOGIN_LOCKOUT_SECONDS", int, 300),
        ("LOGIN_RL_REDIS_TIMEOUT_SEC", int, 1),
        ("JWT_BLACKLIST_CACHE_TTL_SEC", int, 5),
        ("JWT_BLACKLIST_STALE_THRESHOLD_SEC", int, 1800),
    ],
)
def test_default_present_and_typed(key, expected_type, expected_default):
    assert key in Settings.DEFAULTS, f"{key} mancante da Settings.DEFAULTS"
    val = Settings.DEFAULTS[key]
    assert isinstance(val, expected_type), (
        f"{key} = {val!r} ({type(val).__name__}), atteso {expected_type.__name__}"
    )
    assert val == expected_default, (
        f"{key} default {val} != {expected_default} (regressione behavior)"
    )


# Override env via Settings._converti_tipo_env

@pytest.mark.parametrize(
    "key, env_value, expected",
    [
        ("LOGIN_MAX_ATTEMPTS", "5", 5),
        ("LOGIN_WINDOW_SECONDS", "120", 120),
        ("LOGIN_LOCKOUT_SECONDS", "600", 600),
        ("LOGIN_RL_REDIS_TIMEOUT_SEC", "3", 3),
        ("JWT_BLACKLIST_CACHE_TTL_SEC", "2", 2),
        ("JWT_BLACKLIST_STALE_THRESHOLD_SEC", "900", 900),
    ],
)
def test_env_override_typed(monkeypatch, key, env_value, expected):
    monkeypatch.setenv(key, env_value)
    val = settings.get(key)
    assert val == expected
    assert isinstance(val, int)


# Source-bound: ogni consumer legge via settings.get, no os.getenv residuo

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_auth_reads_login_constants_via_settings():
    src = _read(_BACKEND_APP / "core" / "auth" / "auth.py")
    assert "_settings.get(\"LOGIN_MAX_ATTEMPTS\")" in src
    assert "_settings.get(\"LOGIN_WINDOW_SECONDS\")" in src
    assert "_settings.get(\"LOGIN_LOCKOUT_SECONDS\")" in src
    # Niente os.getenv su queste chiavi nel modulo
    assert "os.getenv(\"LOGIN_MAX_ATTEMPTS" not in src
    assert "os.getenv(\"LOGIN_WINDOW_SECONDS" not in src
    assert "os.getenv(\"LOGIN_LOCKOUT_SECONDS" not in src


def test_login_rate_limiter_reads_timeout_via_settings():
    src = _read(_BACKEND_APP / "core" / "security" / "login_rate_limiter.py")
    assert "settings.get(\"LOGIN_RL_REDIS_TIMEOUT_SEC\")" in src
    assert "os.getenv(\"LOGIN_RL_REDIS_TIMEOUT_SEC" not in src


def test_token_blacklist_reads_cache_ttl_via_settings():
    src = _read(_BACKEND_APP / "core" / "security" / "token_blacklist.py")
    assert "settings.get(\"JWT_BLACKLIST_CACHE_TTL_SEC\")" in src
    assert "os.getenv(\"JWT_BLACKLIST_CACHE_TTL_SEC" not in src


def test_admin_system_status_reads_stale_threshold_via_settings():
    src = _read(_BACKEND_APP / "core" / "admin_system_status" / "service.py")
    # alias attuale: `from app import config as app_config` → app_config.settings
    assert "app_config.settings.get(\"JWT_BLACKLIST_STALE_THRESHOLD_SEC\")" in src
    assert "os.getenv(\"JWT_BLACKLIST_STALE_THRESHOLD_SEC" not in src


# auth.LOGIN_* resta module-level + monkeypatch-friendly

def test_auth_login_constants_are_module_level_ints():
    """I test rate-limit esistenti monkeypatch-ano `auth.LOGIN_*`. Se le
    costanti diventassero proprieta'/funzioni il pattern si romperebbe."""
    from app.core.auth import auth as auth_mod
    assert isinstance(auth_mod.LOGIN_MAX_ATTEMPTS, int)
    assert isinstance(auth_mod.LOGIN_WINDOW_SECONDS, int)
    assert isinstance(auth_mod.LOGIN_LOCKOUT_SECONDS, int)
    assert auth_mod.LOGIN_MAX_ATTEMPTS == Settings.DEFAULTS["LOGIN_MAX_ATTEMPTS"]
    assert auth_mod.LOGIN_WINDOW_SECONDS == Settings.DEFAULTS["LOGIN_WINDOW_SECONDS"]
    assert auth_mod.LOGIN_LOCKOUT_SECONDS == Settings.DEFAULTS["LOGIN_LOCKOUT_SECONDS"]


def test_auth_login_constants_are_monkeypatchable(monkeypatch):
    """I test esistenti `test_login_rate_limit_per_ip` patchano queste
    constanti per restringere la finestra. Il pattern deve restare
    valido dopo la migrazione."""
    from app.core.auth import auth as auth_mod
    monkeypatch.setattr(auth_mod, "LOGIN_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(auth_mod, "LOGIN_WINDOW_SECONDS", 5)
    monkeypatch.setattr(auth_mod, "LOGIN_LOCKOUT_SECONDS", 7)
    assert auth_mod.LOGIN_MAX_ATTEMPTS == 2
    assert auth_mod.LOGIN_WINDOW_SECONDS == 5
    assert auth_mod.LOGIN_LOCKOUT_SECONDS == 7
