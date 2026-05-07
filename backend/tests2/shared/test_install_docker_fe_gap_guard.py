"""
Source-bound guard for the Docker / frontend-container gap audit.

BE-GO-READY-INSTALL-018-DOCKER-FE-GAP-AUDIT.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_INSTALL_DOCKER = _REPO_ROOT / "docs" / "ops" / "install-docker.md"
_INSTALL_DRYRUN = (
    _REPO_ROOT / "docs" / "ops" / "install-marco-dry-run.md"
)
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_FRONTEND_DIR = _REPO_ROOT / "frontend"
_WORKFLOWS_DIR = _REPO_ROOT / ".github" / "workflows"


# 0. State of the world (used to gate the negative locks below)


def _frontend_dockerfile_exists() -> bool:
    """True iff at least one Dockerfile-shaped file lives under
    `frontend/` (e.g. `frontend/Dockerfile`, `frontend/Dockerfile.web`)."""
    if not _FRONTEND_DIR.exists():
        return False
    for p in _FRONTEND_DIR.iterdir():
        if not p.is_file():
            continue
        name = p.name.lower()
        if name == "dockerfile" or name.startswith("dockerfile."):
            return True
    return False


def _compose_has_frontend_service() -> bool:
    """True iff `docker-compose.yml` has a service whose key looks
    like a frontend container (`frontend`, `web`, `nginx`, `caddy`,
    `spa`)."""
    if not _COMPOSE.is_file():
        return False
    text = _COMPOSE.read_text(encoding="utf-8")
    # Service keys appear as `  <name>:` two-space-indented under
    # the top-level `services:` mapping. We do not parse YAML;
    # the source-bound substring check is sufficient because the
    # compose schema requires the indentation.
    for needle in (
        "  frontend:",
        "  web:",
        "  nginx:",
        "  caddy:",
        "  spa:",
    ):
        if needle in text:
            return True
    return False


def _ci_invokes_docker_compose() -> bool:
    """True iff at least one workflow under `.github/workflows/`
    calls `docker compose` (current Docker CLI plugin form) or
    `docker-compose` (legacy v1 form) from a `run:` step. Comments
    that mention the string do not count."""
    if not _WORKFLOWS_DIR.exists():
        return False
    pattern = re.compile(
        r"^\s*-\s+run:.*?(docker\s+compose|docker-compose)\b",
        flags=re.MULTILINE,
    )
    for wf in _WORKFLOWS_DIR.glob("*.yml"):
        if pattern.search(wf.read_text(encoding="utf-8")):
            return True
    for wf in _WORKFLOWS_DIR.glob("*.yaml"):
        if pattern.search(wf.read_text(encoding="utf-8")):
            return True
    return False


# 1. Today's truth: backend scaffold yes; FE / full-stack / CI no


def test_no_frontend_dockerfile_today():
    """Today's reality: there is no frontend Dockerfile. If a
    future PR adds one, this test fails -- and that is the
    signal to also flip the negative locks below + add a
    `frontend` service to compose."""
    assert not _frontend_dockerfile_exists(), (
        "A `frontend/Dockerfile*` file now exists. Update "
        "`docs/ops/install-docker.md` `What ships today vs what "
        "does NOT` truth table accordingly, AND add a `frontend` "
        "service to `docker-compose.yml`, AND open the CI lane "
        "(`INSTALL-015-DOCKER-CI`) before flipping the FIRST "
        "SCAFFOLD markers. This test is intentionally a "
        "lock-against-half-migration."
    )


def test_compose_has_no_frontend_service_today():
    """Today's reality: `docker-compose.yml` exposes 5 services
    (postgres, redis, backend, worker, beat) and none of them is
    a frontend container. If a future PR adds one, this test
    fails -- the doc + the FE Dockerfile must move in lockstep."""
    assert not _compose_has_frontend_service(), (
        "`docker-compose.yml` now declares a frontend-shaped "
        "service. Update the install-docker.md truth table AND "
        "ensure `frontend/Dockerfile*` exists AND open the CI "
        "lane before flipping the FIRST SCAFFOLD markers."
    )


def test_ci_does_not_invoke_docker_compose_today():
    """Today's reality: zero workflows under `.github/workflows/`
    invoke `docker compose` (only comments mention it). If a
    future PR adds a `run: docker compose ...` step, this test
    fails -- the install-docker.md `INSTALL-015-DOCKER-CI`
    closure marker should be retired in the same PR."""
    assert not _ci_invokes_docker_compose(), (
        "A workflow under `.github/workflows/` now invokes "
        "`docker compose`. Update `install-docker.md` to retire "
        "the `INSTALL-015-DOCKER-CI` closure marker AND ensure "
        "the truth table reflects the new state."
    )


# 2. Doc-side honesty contract: no claim of full-stack validation


_FORBIDDEN_DOC_PHRASES: tuple[str, ...] = (
    # Phrases that would assert the Docker path is end-to-end
    # validated. Each is a regression of the honesty contract
    # while the three FE/CI artifacts are missing.
    "full-stack Docker is validated",
    "Docker path is validated",
    "Docker is validated",
    "validated full-stack Docker",
    "validated full stack Docker",
    "production-ready Docker install",
    "production-ready Docker deployment",
)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.is_file() else ""


@pytest.mark.architecture
@pytest.mark.docs_guard
@pytest.mark.cleanroom_excluded
def test_install_docker_doc_does_not_claim_full_stack_validation():
    """`docs/ops/install-docker.md` MUST NOT contain any phrase
    that asserts the Docker path is validated. The brief is
    explicit: keep FIRST SCAFFOLD / NOT YET VALIDATED until a
    real report is green AND the FE container + CI lane exist."""
    text = _read(_INSTALL_DOCKER)
    for needle in _FORBIDDEN_DOC_PHRASES:
        assert needle not in text, (
            f"`docs/ops/install-docker.md` contains the "
            f"forbidden validation claim {needle!r}. While "
            "`frontend/Dockerfile*` is missing OR no `frontend` "
            "service is in compose OR no Docker CI lane exists, "
            "the doc MUST NOT claim the Docker path is validated."
        )


@pytest.mark.architecture
@pytest.mark.docs_guard
@pytest.mark.cleanroom_excluded
def test_install_marco_dry_run_does_not_claim_full_stack_validation(
):
    """The dry-run pack must not regress to claiming Docker is
    validated either. Mirror lock so a future doc edit cannot
    silently sneak the claim in via the operator-facing one-pager."""
    text = _read(_INSTALL_DRYRUN)
    for needle in _FORBIDDEN_DOC_PHRASES:
        assert needle not in text, (
            f"`docs/ops/install-marco-dry-run.md` contains the "
            f"forbidden validation claim {needle!r}. The "
            "operator-facing pack must keep parity with "
            "install-docker.md on the FIRST SCAFFOLD / NOT YET "
            "VALIDATED honesty contract."
        )


@pytest.mark.architecture
@pytest.mark.docs_guard
@pytest.mark.cleanroom_excluded
def test_install_docker_doc_keeps_first_scaffold_markers():
    """Positive lock: the literal FIRST SCAFFOLD and NOT YET
    VALIDATED markers must remain in `install-docker.md`. They
    are the load-bearing honesty signal for the whole Docker
    surface."""
    text = _read(_INSTALL_DOCKER)
    assert "FIRST SCAFFOLD" in text, (
        "`docs/ops/install-docker.md` must keep the literal "
        "`FIRST SCAFFOLD` marker."
    )
    assert "NOT YET VALIDATED" in text, (
        "`docs/ops/install-docker.md` must keep the literal "
        "`NOT YET VALIDATED` marker."
    )


# 3. FE-gap subsection present in install-docker.md


@pytest.mark.architecture
@pytest.mark.docs_guard
@pytest.mark.cleanroom_excluded
def test_install_docker_has_fe_gap_audit_subsection():
    """The FE/CI gap audit subsection added by INSTALL-018 must
    survive future edits. It is the single source of truth for
    the question 'does Plenora-ETL on Docker exist today?'."""
    text = _read(_INSTALL_DOCKER)
    assert "BE-GO-READY-INSTALL-018-DOCKER-FE-GAP-AUDIT" in text, (
        "`docs/ops/install-docker.md` must keep the "
        "`BE-GO-READY-INSTALL-018-DOCKER-FE-GAP-AUDIT` stream "
        "tag so the audit trail back to the brief is preserved."
    )
    assert "What ships today vs what does NOT" in text, (
        "`docs/ops/install-docker.md` must keep the explicit "
        "'What ships today vs what does NOT' truth table -- it "
        "is the operator-facing answer to which Docker pieces "
        "actually exist on disk."
    )
    assert 'When can the Docker path be called "validated"?' in text, (
        "`docs/ops/install-docker.md` must keep the explicit "
        "'When can the Docker path be called validated' "
        "subsection naming the three required artifacts."
    )
