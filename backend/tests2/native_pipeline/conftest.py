"""Shared fixtures for the Linux-only native sidecar pipeline tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests2.native_pipeline._fakes import (
    DSN_ENV_VAR,
    DSN_VALUE,
    FakeToolchain,
    build_fake_toolchain,
)


@pytest.fixture(autouse=True, scope="session")
def require_posix_native_runner() -> None:
    if os.name != "posix":
        pytest.skip("native_pipeline requires POSIX process groups")


@pytest.fixture
def fakes(tmp_path: Path) -> FakeToolchain:
    return build_fake_toolchain(tmp_path / "toolchain")


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    target = tmp_path / "work"
    target.mkdir()
    return target


@pytest.fixture
def dsn_env(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(DSN_ENV_VAR, DSN_VALUE)
    return DSN_VALUE
