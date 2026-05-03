"""Contract test BE-CONFIG-001D — feature flags + tuning misc.

Coperti:

1. ogni chiave ha un default tipato in `Settings.DEFAULTS`;
2. ogni consumer legge via `settings.get(...)` (no piu' direct
   `os.getenv` con default inline);
3. l'override env continua a funzionare via type coercion;
4. equivalence pre/post migrazione: i default coincidono con quelli
   storici;
5. niente direct `os.getenv` residuo nei call site migrati (assert
   statica sul source);
6. `_get_pool_size` e `is_bulk_enabled` continuano a comportarsi come
   pre-001D (clamp + fallback).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings, settings


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_APP = Path(__file__).resolve().parent.parent.parent / "app"


# ---------------------------------------------------------------------------
# Defaults present + tipati + valori storici
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "key, expected_type, expected_default",
    [
        ("MSSQL_FAST_EXECUTEMANY", str, "on"),
        ("CARICA_BULK_INSERT", str, "auto"),
        ("ENABLE_ASYNC_ENRICHMENT", bool, False),
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


# ---------------------------------------------------------------------------
# Override env via Settings._converti_tipo_env
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "key, env_value, expected_type, expected",
    [
        ("MSSQL_FAST_EXECUTEMANY", "off", str, "off"),
        ("CARICA_BULK_INSERT", "off", str, "off"),
        ("ENABLE_ASYNC_ENRICHMENT", "true", bool, True),
        ("ENABLE_ASYNC_ENRICHMENT", "1", bool, True),
        ("ENABLE_ASYNC_ENRICHMENT", "yes", bool, True),
        ("ENABLE_ASYNC_ENRICHMENT", "false", bool, False),
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


# ---------------------------------------------------------------------------
# Source-bound: ogni consumer legge via settings.get
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_query_executor_reads_mssql_fast_via_settings():
    src = _read(_BACKEND_APP / "db" / "query_executor.py")
    assert "_settings.get(\"MSSQL_FAST_EXECUTEMANY\")" in src
    assert "_os.getenv(\"MSSQL_FAST_EXECUTEMANY" not in src
    assert "os.getenv(\"MSSQL_FAST_EXECUTEMANY" not in src


def test_bulk_writers_reads_carica_bulk_via_settings():
    src = _read(_BACKEND_APP / "core" / "carica" / "bulk_writers.py")
    assert "_settings.get(\"CARICA_BULK_INSERT\")" in src
    assert "os.getenv(\"CARICA_BULK_INSERT" not in src


def test_enrichment_service_reads_async_flag_via_settings():
    src = _read(_BACKEND_APP / "core" / "arricchisci" / "enrichment_service.py")
    assert "_settings.get(\"ENABLE_ASYNC_ENRICHMENT\")" in src
    assert "os.environ.get(\"ENABLE_ASYNC_ENRICHMENT" not in src


def test_async_http_reads_pool_size_via_settings():
    src = _read(_BACKEND_APP / "shared" / "async_http.py")
    assert "_settings.get(\"ENRICHMENT_POOL_SIZE\")" in src
    assert "os.environ.get(\"ENRICHMENT_POOL_SIZE" not in src


def test_enrichment_execution_engines_reads_pool_size_via_settings():
    src = _read(_BACKEND_APP / "core" / "arricchisci" / "enrichment_execution_engines.py")
    assert "_settings.get(\"ENRICHMENT_POOL_SIZE\")" in src
    assert "os.environ.get(\"ENRICHMENT_POOL_SIZE" not in src


def test_tickets_router_reads_attachment_max_via_settings():
    src = _read(_BACKEND_APP / "core" / "tickets" / "router.py")
    assert "_settings_cfg.get(\"TICKET_ATTACHMENT_MAX_BYTES\")" in src
    assert "os.getenv(\"TICKET_ATTACHMENT_MAX_BYTES" not in src


def test_admin_system_status_reads_db_schema_thresholds_via_settings():
    src = _read(_BACKEND_APP / "core" / "admin_system_status" / "service.py")
    assert "_settings_cfg.get(\"DB_SCHEMA_WARN_GB\")" in src
    assert "_settings_cfg.get(\"DB_SCHEMA_ERROR_GB\")" in src
    assert "os.getenv(\"DB_SCHEMA_WARN_GB" not in src
    assert "os.getenv(\"DB_SCHEMA_ERROR_GB" not in src


# ---------------------------------------------------------------------------
# Behavior helpers (clamp / fallback)
# ---------------------------------------------------------------------------

def test_async_http_pool_size_default_returns_50():
    from app.shared.async_http import _get_pool_size
    assert _get_pool_size() == 50


def test_async_http_pool_size_clamps_low(monkeypatch):
    from app.shared.async_http import _get_pool_size
    monkeypatch.setenv("ENRICHMENT_POOL_SIZE", "1")
    assert _get_pool_size() == 5  # clamp lower bound


def test_async_http_pool_size_clamps_high(monkeypatch):
    from app.shared.async_http import _get_pool_size
    monkeypatch.setenv("ENRICHMENT_POOL_SIZE", "9999")
    assert _get_pool_size() == 200  # clamp upper bound


def test_async_http_pool_size_invalid_falls_back(monkeypatch):
    from app.shared.async_http import _get_pool_size
    # Settings ritorna il default int 50 perche' la coercion fallisce
    # silente ed e' come se non fosse settato.
    monkeypatch.setenv("ENRICHMENT_POOL_SIZE", "not-a-number")
    assert _get_pool_size() == 50


def test_bulk_writers_is_bulk_enabled_default():
    from app.core.carica.bulk_writers import is_bulk_enabled
    assert is_bulk_enabled() is True


@pytest.mark.parametrize("value", ["off", "false", "0", "no", "disabled", "OFF", "False"])
def test_bulk_writers_is_bulk_enabled_off(monkeypatch, value):
    from app.core.carica.bulk_writers import is_bulk_enabled
    monkeypatch.setenv("CARICA_BULK_INSERT", value)
    assert is_bulk_enabled() is False
