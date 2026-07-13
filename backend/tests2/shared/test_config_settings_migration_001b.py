"""
Contract test BE-CONFIG-001B — migrate 4 chiavi a Settings.DEFAULTS.

Coperti:
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings, settings


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_APP = Path(__file__).resolve().parent.parent.parent / "app"
_BACKEND_WORKERS = Path(__file__).resolve().parent.parent.parent / "workers"


# Defaults present + tipati

@pytest.mark.parametrize(
    "key, expected_type, expected_default",
    [
        ("TEMP_ORPHAN_CLEANUP_HOURS", int, 24),
        ("JWT_EXPIRE_HOURS", int, 8),
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
        ("TEMP_ORPHAN_CLEANUP_HOURS", "72", 72),
        ("JWT_EXPIRE_HOURS", "2", 2),
    ],
)
def test_env_override_typed(monkeypatch, key, env_value, expected):
    monkeypatch.setenv(key, env_value)
    val = settings.get(key)
    assert val == expected
    assert isinstance(val, int)


# Consumer reads via settings.get (no direct os.getenv on these keys)

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def removed_result_storage_policy_reads_threshold_via_settings():
    """`result_s3_threshold` deve leggere `settings.get(...)` e non
    avere piu' un default inline duplicato."""
    src = _read(_BACKEND_APP / "core" / "results" / "storage_policy.py")
    assert "settings.get(\"RESULT_S3_THRESHOLD_BYTES\")" in src
    # Niente os.getenv su questa chiave nel modulo
    assert "os.getenv(\"RESULT_S3_THRESHOLD_BYTES" not in src
    assert "os.environ.get(\"RESULT_S3_THRESHOLD_BYTES" not in src


def test_file_storage_dispatch_has_no_threshold():
    src = _read(_BACKEND_APP / "core" / "resources" / "blob_dispatch.py")
    assert "FILE_SOURCE_S3_THRESHOLD_BYTES" not in src
    assert "os.getenv(\"FILE_SOURCE_S3_THRESHOLD_BYTES" not in src
    assert "os.environ.get(\"FILE_SOURCE_S3_THRESHOLD_BYTES" not in src


def test_beat_scheduler_reads_temp_ttl_via_settings():
    src = _read(_BACKEND_WORKERS / "beat_scheduler.py")
    assert "settings.get(\"TEMP_ORPHAN_CLEANUP_HOURS\")" in src
    assert "_os.getenv(\"TEMP_ORPHAN_CLEANUP_HOURS" not in src
    assert "os.getenv(\"TEMP_ORPHAN_CLEANUP_HOURS" not in src


def test_jwt_token_reads_expire_hours_via_settings():
    src = _read(_BACKEND_APP / "core" / "auth" / "jwt_token.py")
    assert "_settings.get(\"JWT_EXPIRE_HOURS\")" in src
    assert "os.getenv(\"JWT_EXPIRE_HOURS" not in src


# Module-level constant + behavior contract

def test_jwt_token_constant_is_int_and_matches_default():
    """`ACCESS_TOKEN_EXPIRE_HOURS` resta int module-level (consumato da
    `auth.auth` + test-contract). Senza override deve coincidere con il
    default Settings."""
    from app.core.auth import jwt_token
    assert isinstance(jwt_token.ACCESS_TOKEN_EXPIRE_HOURS, int)
    assert jwt_token.ACCESS_TOKEN_EXPIRE_HOURS == Settings.DEFAULTS["JWT_EXPIRE_HOURS"]


def test_result_storage_policy_settings_are_removed():
    assert "STORAGE_POLICY" not in Settings.DEFAULTS
    assert "RESULT_S3_THRESHOLD_BYTES" not in Settings.DEFAULTS
    assert "RESULT_DB_HARD_CAP_BYTES" not in Settings.DEFAULTS


def test_result_storage_policy_env_cannot_restore_removed_setting(monkeypatch):
    monkeypatch.setenv("RESULT_S3_THRESHOLD_BYTES", "12345")
    assert "RESULT_S3_THRESHOLD_BYTES" not in Settings.DEFAULTS
