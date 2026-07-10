"""Contratto della topologia worker memory-safe."""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]

_ROOT = Path(__file__).resolve().parents[3]


def _read(relative_path: str) -> str:
    return (_ROOT / relative_path).read_text(encoding="utf-8")


def test_compose_data_plane_is_single_thread_and_queue_scoped():
    compose = _read("docker-compose.yml")

    assert (
        'command: ["--queues", "data-plane", "--processes", "1", '
        '"--threads", "1"]'
    ) in compose
    assert "  worker-control:" in compose
    assert (
        'command: ["--queues", "control-plane", "scheduler", "default", '
        '"--processes", "1", "--threads", "2"]'
    ) in compose


def test_compose_scaling_adds_data_processes_not_threads():
    install_script = _read("scripts/install-docker.sh")
    deploy_script = _read("scripts/deploy-docker.sh")

    for source in (install_script, deploy_script):
        assert '--scale worker="$PLENORA_WORKER_REPLICAS"' in source
        assert "worker worker-control beat" in source


def test_windows_installer_defaults_to_split_single_thread_data_plane():
    installer = _read("scripts/install-native.ps1")

    assert "[int]$WorkerThreads = 1" in installer
    assert "[switch]$SplitWorkerServices = $true" in installer
    assert "[int]$DataWorkerThreads = 1" in installer
    assert (
        "--queues data-plane --processes 1 --threads $DataWorkerThreads"
        in installer
    )
    assert (
        "--queues control-plane scheduler default --processes 1 "
        "--threads $ControlWorkerThreads"
        in installer
    )


def test_documented_local_worker_defaults_are_single_thread():
    paths = (
        "README.md",
        "docs/development/backend.md",
        "docs/development/onboarding.md",
        "docs/ops/install-non-docker.md",
    )
    for path in paths:
        source = _read(path)
        assert "--processes 1 --threads 1" in source, path
        assert "--threads 8" not in source, path


def test_docker_runbooks_start_control_worker():
    paths = (
        "docs/ops/install-docker.md",
        "docs/ops/install-docker-single-server.md",
        "scripts/docker-validate.sh",
        "scripts/install-docker.ps1",
    )
    for path in paths:
        assert "worker-control" in _read(path), path
