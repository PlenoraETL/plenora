"""
Architectural lock for Marco's first-PC clean-install dry-run pack.

BE-GO-READY-INSTALLATION-024-LINUX-CLEAN-INSTALL-DRY-RUN-PACK.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.shared,
    pytest.mark.contract,
    pytest.mark.architecture,
    pytest.mark.docs_guard,
    pytest.mark.cleanroom_excluded,
]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DRY_RUN = _REPO_ROOT / "docs" / "ops" / "install-marco-dry-run.md"
_NON_DOCKER = _REPO_ROOT / "docs" / "ops" / "install-non-docker.md"


@pytest.fixture(scope="module")
def doc_text() -> str:
    assert _DRY_RUN.is_file(), (
        f"docs/ops/install-marco-dry-run.md must exist - "
        "INSTALLATION-024 added the operator dry-run pack. If it "
        "has been deleted, the install-non-docker.md cross-link "
        "(section 13.4 + section 14) must be removed in the same "
        "change."
    )
    return _DRY_RUN.read_text(encoding="utf-8")


def test_dry_run_doc_has_canonical_title(doc_text: str):
    expected = (
        "# Marco's first-PC clean-install dry-run pack\n"
    )
    assert doc_text.startswith(expected), (
        f"docs/ops/install-marco-dry-run.md must start with the "
        f"canonical title {expected!r}. Renaming silently breaks "
        "the cross-link in install-non-docker.md section 13.4."
    )


def test_dry_run_doc_keeps_docker_honest(doc_text: str):
    # The Docker honesty disclaimer is the load-bearing claim of
    # this page. Updated by INSTALL-011: the repo NOW ships a
    # FIRST SCAFFOLD Dockerfile + compose, but the scaffold is
    # NOT YET VALIDATED on a real Linux PC, so the first-PC test
    # path remains the single-machine one. The disclaimer must
    # name both facts so an operator with Docker installed cannot
    # mistake the scaffold for the go-ready path.
    assert "Docker status (honest, NOT the go-ready path)" in doc_text, (
        "The Docker-status callout must keep the literal "
        "'Docker status (honest, NOT the go-ready path)' header "
        "so an operator skimming the page cannot miss it."
    )
    assert "FIRST SCAFFOLD" in doc_text, (
        "The Docker-status callout must explicitly state that "
        "the Dockerfile + compose are FIRST SCAFFOLD (per "
        "INSTALL-011). Without this, an operator may either "
        "miss that the scaffold exists OR mistake it for a "
        "validated path."
    )
    # The disclaimer wraps across a markdown blockquote line
    # break ("> NOT YET\n> VALIDATED"), so we check the
    # canonical anchor phrases independently rather than as a
    # single contiguous substring.
    assert "NOT YET" in doc_text and "VALIDATED" in doc_text, (
        "The Docker-status callout must contain the canonical "
        "'NOT YET VALIDATED end-to-end on a real Linux PC' "
        "phrase (may wrap across a blockquote line break) so "
        "the scaffold is not mistaken for the validated path."
    )
    assert "end-to-end on a real Linux PC" in doc_text, (
        "The Docker-status callout must explicitly say "
        "'end-to-end on a real Linux PC' so the validation gap "
        "is unmistakable."
    )
    assert "BE-GO-READY-INSTALL-011" in doc_text, (
        "The Docker-status callout must reference the "
        "BE-GO-READY-INSTALL-011 stream tag so the audit trail "
        "is preserved."
    )
    # The funnel back to the no-Docker path must remain.
    assert "the only go-ready path for the first pc test is this one" \
        in doc_text.lower(), (
        "The Docker-status callout must explicitly funnel the "
        "operator back to the single-machine path as the only "
        "go-ready first-PC test path. Without the funnel, an "
        "operator might assume the scaffold IS the go-ready "
        "path."
    )


def test_dry_run_doc_documents_required_manual_choices(doc_text: str):
    # The manual-choices table must enumerate the operator
    # decisions that MUST be made before running anything. Each
    # of these is a real choice with no safe default for a first
    # PC test.
    expected = (
        ("ENVIRONMENT",
         "manual-choices table must include ENVIRONMENT."),
        ("Postgres location",
         "manual-choices table must include Postgres location."),
        ("Redis location",
         "manual-choices table must include Redis location."),
        ("Object storage",
         "manual-choices table must include the object storage "
         "decision."),
        ("Backup bucket",
         "manual-choices table must include the backup bucket "
         "(required-non-empty even on db policy)."),
        ("Backend port",
         "manual-choices table must include the backend port."),
        ("Worker count",
         "manual-choices table must include worker count."),
    )
    for needle, message in expected:
        assert needle in doc_text, message


def test_dry_run_doc_uses_minimal_env_profile(doc_text: str):
    # Minimal `.env` profile for the first PC test: STORAGE_POLICY=db,
    # local Postgres, local Redis. The brief is explicit about all
    # three.
    assert "STORAGE_POLICY=db" in doc_text, (
        "Dry-run pack must use STORAGE_POLICY=db as the first-PC "
        "test profile (no buckets, no IAM, no MinIO server)."
    )
    assert (
        "postgresql://plenora:plenora@127.0.0.1:5432/plenora"
        in doc_text
    ), (
        "Dry-run pack must show a literal local Postgres DSN "
        "(127.0.0.1:5432) so the copy/paste sequence works "
        "without DNS / managed-DB setup."
    )
    assert "redis://127.0.0.1:6379/0" in doc_text, (
        "Dry-run pack must show a literal local Redis URL so the "
        "copy/paste sequence works without external Redis."
    )
    assert "PLENORA_INSTALL_BACKUP_BUCKET=plenora-pc-test-no-real-s3" in \
           doc_text, (
        "Dry-run pack must show a literal dummy BACKUP_BUCKET "
        "value so validate_storage_config passes on db policy "
        "without an actual S3 bucket."
    )


def test_dry_run_doc_has_foreground_smoke_for_api_worker_beat(
    doc_text: str,
):
    # Three foreground smoke commands. Foreground (NOT systemd)
    # is intentional: the four boot gates log loudly, and the
    # operator must see them in real time on the first run.
    assert "uvicorn app.main:app" in doc_text, (
        "Dry-run pack must show the foreground uvicorn smoke "
        "command for the API."
    )
    assert (
        "celery -A workers.celery_app worker" in doc_text
    ), (
        "Dry-run pack must show the foreground Celery worker "
        "smoke command."
    )
    assert "--pool=solo" in doc_text, (
        "Dry-run pack must use --pool=solo for the foreground "
        "worker smoke (deterministic, no fork)."
    )
    assert "celery -A workers.celery_app beat" in doc_text, (
        "Dry-run pack must show the foreground Celery beat smoke "
        "command."
    )
    assert "PlenoraBeatScheduler" in doc_text, (
        "Dry-run pack must reference PlenoraBeatScheduler as the "
        "beat -S argument (production / smoke contract)."
    )


def test_dry_run_doc_documents_health_and_ready_outputs(doc_text: str):
    assert "/health" in doc_text and "/ready" in doc_text, (
        "Dry-run pack must reference both /health and /ready."
    )
    # High-level expectation: keys + boolean / status markers,
    # not byte-exact bodies. Lock the canonical key names + the
    # 6 ready checks so a runtime rename does not silently
    # invalidate the doc.
    expected_ready_checks = (
        "postgres",
        "redis",
        "pool_headroom",
        "migrations_aligned",
        "object_storage",
        "reconciliation_backlog",
    )
    for check in expected_ready_checks:
        assert check in doc_text, (
            f"Dry-run pack must document the /ready check name "
            f"{check!r}. The 6 checks are the contract surface for "
            "the readiness gate; renaming one means the docs are "
            "out of sync with `gather_readiness`."
        )
    # The doc must explain the expected first-PC values:
    # object_storage skipped on db policy, reconciliation_backlog
    # at zero on a fresh DB.
    assert "STORAGE_POLICY=db" in doc_text, (
        "Dry-run pack must explain the expected object_storage "
        "ready check value on db policy."
    )
    assert "pending=0 lost=0" in doc_text, (
        "Dry-run pack must explain the expected "
        "reconciliation_backlog detail on a fresh DB."
    )


def test_dry_run_doc_has_failure_triage_checklist(doc_text: str):
    # The triage checklist is the difference between "first PC
    # install works" and "first PC install becomes a debugging
    # afternoon".
    assert "Failure triage checklist" in doc_text, (
        "Dry-run pack must contain the literal 'Failure triage "
        "checklist' heading so the recovery procedure is "
        "discoverable."
    )
    # All four boot gates must be enumerated as separate triage
    # rows so the operator knows which log line to grep.
    expected_gates = (
        "validate_startup",
        "validate_storage_config",
        "init_all_dbs",
        "_repair_psycopg2_ssl_env",
    )
    for gate in expected_gates:
        assert gate in doc_text, (
            f"Triage checklist must enumerate boot gate {gate!r} "
            "as a row so the operator can map a log line to a "
            "fix."
        )


def test_dry_run_doc_flags_destructive_reset(doc_text: str):
    # The total reset is destructive (DROP SCHEMA + FLUSHDB).
    # Without an explicit destructive flag an operator might
    # run it on a real DB.
    assert "DESTRUCTIVE" in doc_text, (
        "Dry-run pack must flag the total-reset as DESTRUCTIVE "
        "so an operator does not run it on a real DB."
    )
    assert "DROP SCHEMA" in doc_text, (
        "Total-reset section must show the DROP SCHEMA SQL "
        "explicitly."
    )
    assert "FLUSHDB" in doc_text, (
        "Total-reset section must show the Redis FLUSHDB step."
    )


def test_dry_run_doc_is_ascii_only(doc_text: str):
    non_ascii = [
        (i, ch) for i, ch in enumerate(doc_text)
        if ord(ch) > 127
    ]
    assert not non_ascii, (
        f"docs/ops/install-marco-dry-run.md must be ASCII-only "
        f"(per scope rule). Found {len(non_ascii)} non-ASCII "
        f"char(s); first at offset {non_ascii[0][0]}: "
        f"U+{ord(non_ascii[0][1]):04X}."
    )


def test_install_non_docker_links_to_dry_run_pack():
    # The cross-link from the full reference page to the
    # dry-run pack must remain. Without it, an operator landing
    # on install-non-docker.md does not discover the dry-run
    # pack and reconstructs the procedure from sections 0-12.
    text = _NON_DOCKER.read_text(encoding="utf-8")
    assert "install-marco-dry-run.md" in text, (
        "docs/ops/install-non-docker.md must link to "
        "install-marco-dry-run.md (section 13.4 callout AND/OR "
        "section 14 related-docs)."
    )
