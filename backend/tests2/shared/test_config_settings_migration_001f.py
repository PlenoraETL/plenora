"""
Contract test BE-CONFIG-001F — lettori env sparsi migrati a Settings.

I 5 siti che leggevano `os.environ.get` direttamente (cap datasource,
cap geo, timeout SharePoint, flag cleanroom del beat) ora passano da
`Settings.DEFAULTS` + `settings.get`. Regola del config boundary
(2026-07-04): segreti SOLO in .env, tutto il resto ha default tipato
in DEFAULTS con override env opzionale. Il lock anti-regressione e'
`test_architecture_legacy_patterns::test_os_environ_get_only_at_boundary_modules`.
"""

from __future__ import annotations

import pytest

from app.config import Settings, settings


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_MIGRATED_KEYS = (
    ("PLENORA_DATASOURCE_SOURCE_MAX_BYTES", int, 2 * 1024 * 1024 * 1024),
    ("PLENORA_DATASOURCE_SCHEMA_MAX_BYTES", int, 50 * 1024 * 1024),
    ("PLENORA_GEO_VORONOI_MAX_POINTS", int, 100_000),
    ("PLENORA_GEO_CLEAN_TOPOLOGY_MAX_GEOMS", int, 50_000),
    ("PLENORA_SHAREPOINT_CONNECT_TIMEOUT_SEC", float, 15.0),
    ("PLENORA_SHAREPOINT_STREAM_TIMEOUT_SEC", float, 300.0),
    ("PLENORA_CLEANROOM_BEAT_PING", bool, False),
)


@pytest.mark.parametrize("key, tipo, default", _MIGRATED_KEYS)
def test_migrated_key_default_present_and_typed(key, tipo, default):
    assert key in Settings.DEFAULTS, f"{key} deve avere default in DEFAULTS"
    val = Settings.DEFAULTS[key]
    assert type(val) is tipo, f"{key}: default {val!r} non e' {tipo.__name__}"
    assert val == default


@pytest.mark.parametrize(
    "key, raw, expected",
    [
        ("PLENORA_DATASOURCE_SOURCE_MAX_BYTES", "1048576", 1048576),
        ("PLENORA_GEO_VORONOI_MAX_POINTS", "5000", 5000),
        ("PLENORA_SHAREPOINT_CONNECT_TIMEOUT_SEC", "2.5", 2.5),
        ("PLENORA_CLEANROOM_BEAT_PING", "true", True),
        ("PLENORA_CLEANROOM_BEAT_PING", "1", True),
    ],
)
def test_migrated_key_env_override_with_type_coercion(
    monkeypatch, key, raw, expected
):
    monkeypatch.setenv(key, raw)
    assert settings.get(key) == expected


def test_clamp_non_positive_values_fall_back_to_default(monkeypatch):
    """I cap <=0 ricadono sul default nei call-site helper (semantica
    preservata dalla migrazione)."""
    from app.core.manipola.transforms.spatial import _env_int

    monkeypatch.setenv("PLENORA_GEO_VORONOI_MAX_POINTS", "0")
    assert _env_int("PLENORA_GEO_VORONOI_MAX_POINTS", 100_000) == 100_000

    monkeypatch.setenv("PLENORA_GEO_VORONOI_MAX_POINTS", "-5")
    assert _env_int("PLENORA_GEO_VORONOI_MAX_POINTS", 100_000) == 100_000


def test_beat_cleanroom_ping_flag_via_settings(monkeypatch):
    from app.core.scheduler.beat_schedule_config import beat_schedule_config

    monkeypatch.delenv("PLENORA_CLEANROOM_BEAT_PING", raising=False)
    assert "cleanroom-beat-ping" not in beat_schedule_config()

    monkeypatch.setenv("PLENORA_CLEANROOM_BEAT_PING", "1")
    assert "cleanroom-beat-ping" in beat_schedule_config()
