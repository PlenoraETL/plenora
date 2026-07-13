"""
Collectable clean-install simulation contract.

BE-1.0-BATCH-077-W5-CLEAN-INSTALL-SIMULATION.

This test does not start Docker, Postgres, Redis, uvicorn, or Celery.
It locks the command/config wiring that makes the real clean-install
simulation reproducible on a Linux host.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.shared,
    pytest.mark.contract,
    pytest.mark.install_validation,
]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_INSTALL_SH = _REPO_ROOT / "scripts" / "install.sh"
_DOCKER_VALIDATE_SH = _REPO_ROOT / "scripts" / "docker-validate.sh"
_DOCKER_API_SMOKE_SH = _REPO_ROOT / "scripts" / "docker-api-smoke.sh"


def _text(path: Path) -> str:
    assert path.is_file(), f"Expected file to exist: {path}"
    return path.read_text(encoding="utf-8")


def _assert_ordered(text: str, markers: tuple[str, ...]) -> None:
    last = -1
    for marker in markers:
        pos = text.find(marker)
        assert pos != -1, f"Missing clean-install marker: {marker!r}"
        assert pos > last, (
            f"Clean-install marker {marker!r} appears out of order. "
            f"Expected order: {markers!r}"
        )
        last = pos


def test_non_docker_installer_exposes_minimal_clean_install_config():
    """The guided installer must be able to produce the minimal runtime
    config needed by a clean boot with the S3 data plane."""
    text = _text(_INSTALL_SH)

    required_inputs = (
        "PLENORA_INSTALL_DATABASE_URL",
        "PLENORA_INSTALL_DB_SCHEMA",
        "PLENORA_INSTALL_BROKER_URL",
        "PLENORA_INSTALL_BACKUP_BUCKET",
        "PLENORA_INSTALL_OBJECT_STORAGE_BUCKET",
        "PLENORA_INSTALL_S3_ENDPOINT_URL",
        "PLENORA_INSTALL_ENVIRONMENT",
        "PLENORA_INSTALL_BACKEND_PORT",
    )
    for marker in required_inputs:
        assert marker in text, (
            f"scripts/install.sh must expose {marker} for a "
            "non-interactive clean-install simulation."
        )

    defaults = (
        ('prompt DB_SCHEMA', '"plenora"'),
        ('prompt BROKER_URL', '"amqp://guest:guest@localhost:5672"'),
        ('prompt BACKUP_BUCKET', '"plenora-backups"'),
        ('prompt BACKEND_PORT', '"5004"'),
    )
    for prompt_marker, default_marker in defaults:
        start = text.find(prompt_marker)
        assert start != -1, f"Missing installer prompt {prompt_marker!r}"
        block = text[start : start + 240]
        assert default_marker in block, (
            f"{prompt_marker} must keep default {default_marker} so the "
            "minimal clean-install path remains reproducible."
        )

    managed_keys = (
        "DATABASE_URL",
        "DB_SCHEMA",
        "DRAMATIQ_BROKER_URL",
        "PLENORA_ENCRYPTION_KEY",
        "JWT_SECRET_KEY",
        "BACKUP_BUCKET",
        "OBJECT_STORAGE_BACKEND",
        "OBJECT_STORAGE_BUCKET",
        "S3_ENDPOINT_URL",
        "PLENORA_BACKEND_PORT",
    )
    for key in managed_keys:
        assert key in text, (
            f"Installer-managed key {key!r} must remain in "
            "scripts/install.sh so a clean backend/.env contains the "
            "minimum boot/readiness inputs."
        )


def test_docker_validate_models_empty_db_boot_readiness_and_smoke_order():
    """The Docker validation runner is the reproducible empty-DB
    simulation: persistent volumes start empty on a fresh host, backend
    boot applies DB bootstrap/migrations, readiness proves it, then the
    worker smoke task runs."""
    text = _text(_DOCKER_VALIDATE_SH)

    _assert_ordered(
        text,
        (
            'run_step 3a "up postgres + rabbitmq + object storage"',
            'run_step 3b "up backend (gates on HEALTHCHECK)"',
            'run_step 4a "GET /health"',
            'run_step 4b "GET /ready"',
            "\nrun_schema_postcondition\n",
            'run_step 5 "up worker + beat"',
            'run_step 6a "up frontend (static nginx)"',
            'run_step 6b "GET frontend SPA shell"',
            'run_step 7 "cleanroom.ping round-trip"',
            'run_step 8 "memory caps visibility"',
            'run_step 9 "non-destructive stop (preserves volumes)"',
        ),
    )

    postcondition_markers = (
        "SELECT EXISTS (SELECT 1 FROM information_schema.schemata "
        "WHERE schema_name = 'plenora');",
        "SELECT to_regclass('plenora.schema_migrations') IS NOT NULL;",
        "SELECT count(*) FROM plenora.schema_migrations;",
        "schema_migrations_rows=",
    )
    for marker in postcondition_markers:
        assert marker in text, (
            f"Empty-DB simulation must prove schema/migration marker "
            f"{marker!r} after /ready."
        )

    assert "docker compose down" not in _strip_shell_comments(text), (
        "The clean-install simulation wrapper must stay non-destructive; "
        "operators opt into volume deletion outside this runner."
    )


def test_smoke_commands_are_bounded_and_include_full_api_acceptance():
    """Install smoke remains bounded; Docker API smoke now covers auth/project."""
    install_text = _text(_INSTALL_SH)
    api_smoke_text = _text(_DOCKER_API_SMOKE_SH)

    for marker in (
        "PLENORA_SMOKE_BACKEND_URL",
        "curl -fsS --max-time 5",
        "curl -fsS --max-time 10",
        "PLENORA_SMOKE_RUN_PING",
        # Plenora 2.0 Fase 5: la smoke cleanroom.ping polla task_results su PG
        # con una deadline limitata (era ar.get(timeout=30) di Celery/Redis).
        "time.monotonic() + 30",
    ):
        assert marker in install_text, (
            f"scripts/install.sh --smoke must keep bounded marker "
            f"{marker!r} for the post-install smoke path."
        )

    for marker in (
        "--infrastructure-only",
        'run_get_check "/health"',
        'run_get_check "/ready"',
        'curl_json "POST" "/api/auth/login"',
        'curl_json "GET" "/api/auth/me"',
        'curl_json "POST" "/api/projects"',
        'curl_json "DELETE" "/api/projects/${PROJECT_ID}"',
        'curl_json "POST" "/api/auth/logout"',
        "Full Docker API smoke passed.",
    ):
        assert marker in api_smoke_text, (
            f"scripts/docker-api-smoke.sh must keep marker {marker!r} "
            "so Docker acceptance proves a real authenticated API flow."
        )


def _strip_shell_comments(source: str) -> str:
    lines = []
    for line in source.splitlines():
        if line.lstrip().startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)
