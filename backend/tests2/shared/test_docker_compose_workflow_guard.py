"""
Source-bound guards for the Docker Compose CI smoke workflow.

Batch 108 W6: Docker CI must go beyond config/build and prove the
compose backend reaches /health and /ready without destructive teardown.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "docker-compose.yml"


def _workflow_text() -> str:
    assert _WORKFLOW.is_file(), (
        ".github/workflows/docker-compose.yml must exist as the Docker "
        "Compose CI lane."
    )
    return _WORKFLOW.read_text(encoding="utf-8")


def _assert_ordered(text: str, markers: tuple[str, ...]) -> None:
    last = -1
    for marker in markers:
        pos = text.find(marker)
        assert pos != -1, f"Missing workflow marker: {marker!r}"
        assert pos > last, (
            f"Workflow marker {marker!r} appears out of order. "
            f"Expected order: {markers!r}"
        )
        last = pos


def test_docker_compose_workflow_runs_backend_smoke_after_build():
    text = _workflow_text()
    _assert_ordered(
        text,
        (
            "docker compose config",
            "docker compose build backend frontend",
            "docker compose up -d --wait postgres rabbitmq object-storage",
            "docker compose up -d backend",
            "bash scripts/docker-api-smoke.sh --infrastructure-only",
            "python -m app.core.auth.smoke_seed",
            "Run full Docker API smoke",
        ),
    )


def test_docker_compose_workflow_uses_bounded_api_polling_script():
    text = _workflow_text()
    for marker in (
        "PLENORA_ACCEPTANCE_TIMEOUT_SECONDS",
        "PLENORA_ACCEPTANCE_POLL_INTERVAL_SECONDS",
        "PLENORA_ACCEPTANCE_CURL_MAX_TIME_SECONDS",
        "scripts/docker-api-smoke.sh",
        "PLENORA_SMOKE_USERNAME",
        "PLENORA_SMOKE_PASSWORD",
    ):
        assert marker in text, (
            f"Docker Compose CI must keep bounded backend smoke marker "
            f"{marker!r}. The workflow should poll /health and /ready "
            "through the dedicated smoke script instead of a one-shot curl."
        )


def test_docker_compose_workflow_cleans_up_isolated_resources():
    text = _workflow_text()
    _assert_ordered(
        text,
        (
            "docker network create plenora",
            "Run full Docker API smoke",
            "if: always()",
            "docker compose down -v",
            "docker network rm plenora || true",
        ),
    )
    banned_markers = (
        "docker volume rm",
        "docker system prune",
    )
    offenders = [marker for marker in banned_markers if marker in text]
    assert not offenders, (
        "Docker Compose CI smoke must only clean up its isolated resources. "
        "Broad destructive markers "
        f"found: {offenders}."
    )


def test_docker_compose_workflow_watches_api_smoke_script_changes():
    text = _workflow_text()
    occurrences = text.count("scripts/docker-api-smoke.sh")
    assert occurrences >= 3, (
        "The Docker Compose workflow must include scripts/docker-api-smoke.sh "
        "in both PR and push path filters and invoke it in the smoke step."
    )
