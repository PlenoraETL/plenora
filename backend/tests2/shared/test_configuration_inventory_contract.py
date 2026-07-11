"""Guards for the canonical application configuration inventory."""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


REMOVED_DEAD_KEYS = frozenset(
    {
        "DB_CONNECT_TIMEOUT",
        "LOG_DB_CONNECT_TIMEOUT",
        "LOG_DB_BUSY_TIMEOUT",
        "SUPPORTED_DB_TYPES",
        "TASK_PROGRESS_POLL_INTERVAL",
        "TASK_TIMEOUT",
        "TASK_PREVIEW_TIMEOUT",
        "TASK_PREVIEW_HARD_LIMIT",
        "TASK_SCHEDULE_TIMEOUT",
        "SUPPORTED_OUTPUT_FORMATS",
        "DEFAULT_OUTPUT_FORMAT",
        "SUPPORTED_FILE_FORMATS",
        "SUPPORTED_PROJECT_TYPES",
        "MAX_PROJECT_NAME_LENGTH",
        "MAX_CONNECTION_NAME_LENGTH",
        "ENRICHMENT_BATCH_SIZE",
        "ENRICHMENT_MAX_WORKERS",
        "ENRICHMENT_CACHE_TTL",
    }
)


def test_dead_configuration_keys_do_not_return_to_defaults():
    from app.config import Settings

    assert REMOVED_DEAD_KEYS.isdisjoint(Settings.DEFAULTS)


def test_every_default_is_represented_outside_admin_and_config_display():
    """Una voce mostrata soltanto in UI non deve sembrare un tuning attivo."""
    from app.config import Settings

    backend_root = Path(__file__).resolve().parents[2]
    excluded = {
        (backend_root / "app" / "config.py").resolve(),
        (backend_root / "app" / "config_display.py").resolve(),
        (
            backend_root
            / "app"
            / "core"
            / "admin_runtime_config"
            / "service.py"
        ).resolve(),
    }
    runtime_files = [
        path
        for package in (backend_root / "app", backend_root / "workers")
        for path in package.rglob("*.py")
        if path.resolve() not in excluded
    ]
    runtime_source = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in runtime_files
    )
    display_only = {
        key for key in Settings.DEFAULTS if key not in runtime_source
    }
    assert display_only == set()


def test_postgres_pool_defaults_have_one_canonical_owner():
    from app.config import Settings

    assert {
        "DB_POOL_MIN": 2,
        "DB_POOL_MAX": 12,
        "DB_POOL_TIMEOUT_SECONDS": 30,
        "DB_IDLE_IN_TRANSACTION_TIMEOUT_MS": 300_000,
    }.items() <= Settings.DEFAULTS.items()

    source = (
        Path(__file__).resolve().parents[2] / "app" / "db" / "pg_connection.py"
    ).read_text(encoding="utf-8")
    assert "settings.get_raw(name)" in source
    assert '_read_int_env("DB_POOL_MIN")' in source
    assert '_read_int_env("DB_POOL_MAX")' in source


def test_admin_catalog_only_has_explicit_required_env_outside_defaults():
    from app.config import Settings
    from app.core.admin_runtime_config.service import _CONFIG_SECTIONS

    catalog_keys = {key for _, keys in _CONFIG_SECTIONS for key in keys}
    required_or_contextual_env = {
        "BACKUP_BUCKET",
        "DATABASE_URL",
        "ENVIRONMENT",
        "JWT_SECRET_KEY",
        "PLENORA_ENCRYPTION_KEY",
    }
    assert catalog_keys - set(Settings.DEFAULTS) == required_or_contextual_env


def test_backend_marks_essential_settings_for_frontend():
    from app.core.admin_runtime_config.service import _setting_entry

    assert _setting_entry("DATA_PLANE_EXECUTION_MODE")["essential"] is True
    assert _setting_entry("DATABASE_URL")["essential"] is True
    assert _setting_entry("LOG_DB_MAX_ROWS")["essential"] is False
