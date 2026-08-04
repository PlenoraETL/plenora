"""Toolchain configuration is pinned, explicit and disabled by default."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import settings
from app.core.native_pipeline import (
    NativePipelineDisabled,
    NativeToolchainConfig,
    run_native_pipeline,
)
from app.core.native_pipeline.config import (
    EXPECTED_DATABASE_VERSION,
    EXPECTED_DATA_VERSION,
    EXPECTED_IO_VERSION,
    IDENTITY_MODE_SHA256,
)
from tests2.native_pipeline._fakes import FakeToolchain, build_config, build_request


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.security]


_ENV_KEYS = (
    "NATIVE_PIPELINE_ENABLED",
    "NATIVE_PIPELINE_DATABASE_ARGV",
    "NATIVE_PIPELINE_DATA_ARGV",
    "NATIVE_PIPELINE_IO_ARGV",
    "NATIVE_PIPELINE_DATABASE_VERSION",
    "NATIVE_PIPELINE_DATA_VERSION",
    "NATIVE_PIPELINE_IO_VERSION",
    "NATIVE_PIPELINE_DATABASE_IDENTITY_MODE",
    "NATIVE_PIPELINE_DATA_IDENTITY_MODE",
    "NATIVE_PIPELINE_IO_IDENTITY_MODE",
    "NATIVE_PIPELINE_DATABASE_IDENTITY_PATH",
    "NATIVE_PIPELINE_DATA_IDENTITY_PATH",
    "NATIVE_PIPELINE_IO_IDENTITY_PATH",
    "NATIVE_PIPELINE_DATABASE_SHA256",
    "NATIVE_PIPELINE_DATA_SHA256",
    "NATIVE_PIPELINE_IO_SHA256",
    "NATIVE_PIPELINE_WORKSPACE_DIR",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_versions_are_pinned_to_the_qualified_releases() -> None:
    assert EXPECTED_DATABASE_VERSION == "1.1.0"
    assert EXPECTED_DATA_VERSION == "1.0.2"
    assert EXPECTED_IO_VERSION == "1.0.1"


def test_settings_defaults_keep_the_native_path_disabled() -> None:
    assert settings.get("NATIVE_PIPELINE_ENABLED") is False
    assert NativeToolchainConfig.from_settings().enabled is False


def test_settings_defaults_configure_no_binary() -> None:
    config = NativeToolchainConfig.from_settings()

    assert config.database.argv_prefix == ()
    assert config.data.argv_prefix == ()
    assert config.io.argv_prefix == ()


def test_settings_defaults_pin_the_expected_identities() -> None:
    config = NativeToolchainConfig.from_settings()

    assert config.database.identity == "plenora-database"
    assert config.data.identity == "plenora-data-tools"
    assert config.io.identity == "plenora-io"
    assert config.database.version == EXPECTED_DATABASE_VERSION
    assert config.data.version == EXPECTED_DATA_VERSION
    assert config.io.version == EXPECTED_IO_VERSION
    assert config.database.identity_mode == IDENTITY_MODE_SHA256
    assert config.data.identity_mode == IDENTITY_MODE_SHA256
    assert config.io.identity_mode == IDENTITY_MODE_SHA256


def test_security_limits_have_bounded_defaults() -> None:
    config = NativeToolchainConfig.from_settings()

    assert config.max_binary_bytes == 512 * 1024 * 1024
    assert config.max_stage_artifact_bytes == 512 * 1024 * 1024
    assert config.max_plan_bytes == 16 * 1024 * 1024


def test_argv_prefix_is_exactly_one_absolute_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = str((tmp_path / "plenora-database").resolve())
    monkeypatch.setenv(
        "NATIVE_PIPELINE_DATABASE_ARGV",
        json.dumps([executable]),
    )

    config = NativeToolchainConfig.from_settings()

    assert config.database.argv_prefix == (executable,)


@pytest.mark.parametrize(
    "raw",
    [
        "plenora-database --flag",
        '"plenora-database"',
        "[]",
        "[1, 2]",
        "{}",
        '["relative-tool"]',
        '["/opt/tool", "--wrapper-arg"]',
    ],
)
def test_malformed_argv_prefix_is_rejected(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("NATIVE_PIPELINE_DATABASE_ARGV", raw)

    with pytest.raises(ValueError):
        NativeToolchainConfig.from_settings()


def test_enabled_flag_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NATIVE_PIPELINE_ENABLED", "true")

    assert NativeToolchainConfig.from_settings().enabled is True


def test_disabled_config_refuses_to_run(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    request = build_request(workdir)

    with pytest.raises(NativePipelineDisabled) as excinfo:
        run_native_pipeline(request, build_config(fakes, enabled=False))

    assert excinfo.value.code == "native_pipeline_disabled"
    assert fakes.invocations() == []
    assert not request.output_path.exists()


def test_workspace_root_defaults_to_process_temp() -> None:
    assert NativeToolchainConfig.from_settings().workspace_root is None


def test_config_is_frozen() -> None:
    config = NativeToolchainConfig.from_settings()

    with pytest.raises((AttributeError, TypeError)):
        config.enabled = True  # type: ignore[misc]
