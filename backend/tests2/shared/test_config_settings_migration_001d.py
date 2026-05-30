"""
Contract test BE-CONFIG-001D — tuning misc.

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
        ("MSSQL_FAST_EXECUTEMANY", str, "on"),
        ("ENRICHMENT_POOL_SIZE", int, 50),
        ("TICKET_ATTACHMENT_MAX_BYTES", int, 10 * 1024 * 1024),
        ("DB_SCHEMA_WARN_GB", float, 1.0),
        ("DB_SCHEMA_ERROR_GB", float, 5.0),
    ],
)
def test_default_present_and_typed(key, expected_type, expected_default):
    assert key in Settings.DEFAULTS, f"{key} mancante da Settings.DEFAULTS"
    val = Settings.DEFAULTS[key]
    assert isinstance(val, expected_type), (
        f"{key} = {val!r} ({type(val).__name__}), atteso {expected_type.__name__}"
    )
    assert val == expected_default, (
        f"{key} default {val!r} != {expected_default!r} (regressione behavior)"
    )


# Override env via Settings._converti_tipo_env

@pytest.mark.parametrize(
    "key, env_value, expected_type, expected",
    [
        ("MSSQL_FAST_EXECUTEMANY", "off", str, "off"),
        ("ENRICHMENT_POOL_SIZE", "42", int, 42),
        ("TICKET_ATTACHMENT_MAX_BYTES", "99", int, 99),
        ("DB_SCHEMA_WARN_GB", "2.5", float, 2.5),
        ("DB_SCHEMA_ERROR_GB", "7.5", float, 7.5),
    ],
)
def test_env_override_typed(monkeypatch, key, env_value, expected_type, expected):
    monkeypatch.setenv(key, env_value)
    val = settings.get(key)
    assert val == expected
    assert isinstance(val, expected_type), (
        f"{key}={env_value!r} -> {val!r} ({type(val).__name__}), atteso {expected_type.__name__}"
    )


# Source-bound: ogni consumer legge via settings.get

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_query_engine_factory_reads_mssql_fast_via_settings():
    # Il query-execution layer e' stato relocato da app/db a
    # app/core/query_execution (shim app/db/query_engines.py rimosso).
    src = _read(_BACKEND_APP / "core" / "query_execution" / "query_engines.py")
    assert "_settings.get(\"MSSQL_FAST_EXECUTEMANY\")" in src
    assert "_os.getenv(\"MSSQL_FAST_EXECUTEMANY" not in src
    assert "os.getenv(\"MSSQL_FAST_EXECUTEMANY" not in src


def test_async_http_reads_pool_size_via_settings():
    src = _read(_BACKEND_APP / "infrastructure" / "http" / "async_client.py")
    assert "_settings.get(\"ENRICHMENT_POOL_SIZE\")" in src
    assert "os.environ.get(\"ENRICHMENT_POOL_SIZE" not in src


def test_tickets_router_reads_attachment_max_via_settings():
    src = _read(_BACKEND_APP / "core" / "tickets" / "router.py")
    assert "_settings_cfg.get(\"TICKET_ATTACHMENT_MAX_BYTES\")" in src
    assert "os.getenv(\"TICKET_ATTACHMENT_MAX_BYTES" not in src


def test_admin_system_status_reads_db_schema_thresholds_via_settings():
    src = _read(_BACKEND_APP / "core" / "admin_system_status" / "service.py")
    # alias attuale: `from app import config as app_config` → app_config.settings
    assert "app_config.settings.get(\"DB_SCHEMA_WARN_GB\")" in src
    assert "app_config.settings.get(\"DB_SCHEMA_ERROR_GB\")" in src
    assert "os.getenv(\"DB_SCHEMA_WARN_GB" not in src
    assert "os.getenv(\"DB_SCHEMA_ERROR_GB" not in src


# Behavior helpers (clamp / fallback)

def test_async_http_pool_size_default_returns_50():
    from app.infrastructure.http.async_client import _get_pool_size
    assert _get_pool_size() == 50


def test_async_http_pool_size_clamps_low(monkeypatch):
    from app.infrastructure.http.async_client import _get_pool_size
    monkeypatch.setenv("ENRICHMENT_POOL_SIZE", "1")
    assert _get_pool_size() == 5  # clamp lower bound


def test_async_http_pool_size_clamps_high(monkeypatch):
    from app.infrastructure.http.async_client import _get_pool_size
    monkeypatch.setenv("ENRICHMENT_POOL_SIZE", "9999")
    assert _get_pool_size() == 200  # clamp upper bound


def test_async_http_pool_size_invalid_falls_back(monkeypatch):
    from app.infrastructure.http.async_client import _get_pool_size
    # Settings ritorna il default int 50 perche' la coercion fallisce
    # silente ed e' come se non fosse settato.
    monkeypatch.setenv("ENRICHMENT_POOL_SIZE", "not-a-number")
    assert _get_pool_size() == 50
