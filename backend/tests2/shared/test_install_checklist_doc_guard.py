"""Architectural lock for the operator-facing clean-install checklist.

BE-GO-READY-INSTALLATION-023-CLEAN-INSTALL-DOCKER-SINGLE-MACHINE-CHECKLIST.

Source-bound assertions on `docs/ops/install-non-docker.md`
section 13 (Operator quick-reference checklist). The section
is the single page Marco (or any first-time installer) reads
to perform a real Linux PC install. If the checklist drifts
out of sync with the runtime - or if it ever pretends Docker
alone is sufficient - operators get a broken install.

Locks today:

  - section 13 with the canonical header exists;
  - 13.1 explicitly classifies Docker as NOT INSTALLABLE
    today (no Dockerfile, no compose);
  - 13.2 covers the 11 critical install steps (OS packages,
    Python venv, Postgres, Redis, object storage choice,
    Celery worker/beat count, env/secrets, migrations, smoke,
    rollback);
  - 13.2.1 documents the four object storage options
    (disabled / local / S3-compatible / AWS) AND classifies
    "local" as NOT IMPLEMENTED;
  - 13.2.2 references `scripts/install.sh` so the operator
    is funneled through the idempotent installer instead of
    a manual rebuild every time;
  - 13.2.3 documents the rollback procedure so a failed PC
    test can be cleaned without manual archaeology;
  - 13.2.4 calls out the four critical gotchas that surface
    in real installs (schema autocreate, single Redis URL,
    object storage cap on db policy, cleanroom != prod);
  - 13.4 has the literal command sequence Marco can copy/paste;
  - section is ASCII-only.

Outside scope:
  - the detailed step content (locked by their own sections
    1 to 11);
  - the cleanroom drift table (locked by
    `test_cleanroom_celery_drift_doc_guard.py`);
  - the install script packaging contract (locked by
    `test_install_script_packaging_guard.py`).
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOC = _REPO_ROOT / "docs" / "ops" / "install-non-docker.md"

_CHECKLIST_HEADER = (
    "## 13. Operator quick-reference checklist (real Linux PC test)"
)


@pytest.fixture(scope="module")
def doc_text() -> str:
    return _DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def checklist_section(doc_text: str) -> str:
    if _CHECKLIST_HEADER not in doc_text:
        return ""
    start = doc_text.index(_CHECKLIST_HEADER)
    # Section 13 ends at the next top-level `## 14.` Related docs.
    end_marker = "\n## 14. Related docs"
    end = doc_text.find(end_marker, start)
    if end == -1:
        end = len(doc_text)
    return doc_text[start:end]


def test_checklist_section_13_exists(doc_text: str):
    assert _CHECKLIST_HEADER in doc_text, (
        f"docs/ops/install-non-docker.md must contain section 13 "
        f"with header {_CHECKLIST_HEADER!r}. The section is the "
        "operator-facing single-page checklist for a real Linux "
        "first-time install. Removing it forces operators to "
        "reconstruct the procedure from the detailed sections, "
        "which is error-prone."
    )


def test_checklist_documents_docker_path_not_installable(
    checklist_section: str,
):
    # The Docker path is intentionally and EXPLICITLY a stub.
    # The checklist must funnel operators with Docker installed
    # to Path B (single-machine), NOT mislead them into
    # `docker compose up`.
    assert "13.1 Path A: Docker / compose" in checklist_section, (
        "Section 13.1 must exist and be titled 'Path A: Docker / "
        "compose' so the Docker option is named explicitly."
    )
    assert "NOT INSTALLABLE TODAY" in checklist_section, (
        "Section 13.1 must contain the literal 'NOT INSTALLABLE "
        "TODAY' so an operator skimming the checklist cannot miss "
        "that the Docker path is not real."
    )
    assert "Having Docker installed does not change" in checklist_section, (
        "Section 13.1 must explicitly state that Docker pre-install "
        "on the PC does not change the install procedure (operator "
        "must use Path B). This is the load-bearing claim that "
        "prevents 'I have Docker, surely there's a compose file' "
        "operator confusion."
    )


def test_checklist_covers_all_critical_install_steps(
    checklist_section: str,
):
    # 11 critical operator-facing steps. Each must be named in the
    # checklist text so the Step-by-step block is complete. Match
    # by canonical step phrases that anchor to the runtime.
    expected_step_anchors = (
        ("Step 1 - OS packages",
         "Section 13 must include Step 1 OS packages."),
        ("Step 2 - Application user",
         "Section 13 must include Step 2 application user setup."),
        ("Step 3 - PostgreSQL",
         "Section 13 must include Step 3 Postgres provisioning."),
        ("Step 4 - Redis",
         "Section 13 must include Step 4 Redis provisioning."),
        ("Step 5 - Object storage",
         "Section 13 must include Step 5 object storage decision."),
        ("Step 6 - Secrets",
         "Section 13 must include Step 6 secrets decision."),
        ("Step 7 - Checkout + venv",
         "Section 13 must include Step 7 checkout + venv + .env."),
        ("Step 8 - Migrations",
         "Section 13 must include Step 8 migrations / DB init."),
        ("Step 9 - systemd",
         "Section 13 must include Step 9 systemd unit install."),
        ("Step 10 - Frontend",
         "Section 13 must include Step 10 frontend build + serve."),
        ("Step 11 - Smoke",
         "Section 13 must include Step 11 smoke test."),
    )
    for needle, message in expected_step_anchors:
        assert needle in checklist_section, message


def test_checklist_documents_four_object_storage_options(
    checklist_section: str,
):
    # The decision matrix in 13.2.1 must enumerate the four
    # object storage options - hiding any of them lets an
    # operator with MinIO experience think "local" is real.
    expected_phrases = (
        ("disabled", "section 13.2.1 must mention 'disabled' "
         "(STORAGE_POLICY=db) as one of the four options."),
        ("local", "section 13.2.1 must mention 'local' as one of "
         "the four options (even though it is NOT IMPLEMENTED)."),
        ("S3-compatible", "section 13.2.1 must mention "
         "'S3-compatible' (MinIO) as one of the four options."),
        ("AWS", "section 13.2.1 must mention 'AWS' as one of the "
         "four options."),
        ("STORAGE_POLICY=db", "section 13.2.1 must reference the "
         "exact env value STORAGE_POLICY=db so operators copy/paste."),
        ("CLOUD_PROVIDER=minio", "section 13.2.1 must reference "
         "the exact env value CLOUD_PROVIDER=minio."),
        ("CLOUD_PROVIDER=aws", "section 13.2.1 must reference the "
         "exact env value CLOUD_PROVIDER=aws."),
    )
    for needle, message in expected_phrases:
        assert needle in checklist_section, message
    # The "local" option must be clearly flagged as NOT IMPLEMENTED -
    # it is an attractive trap for operators who want a non-cloud,
    # non-MinIO setup.
    assert "OBJECT_STORAGE_BACKEND=local" in checklist_section, (
        "Section 13.2.1 must reference OBJECT_STORAGE_BACKEND=local "
        "to anchor the trap-flag for the (currently NOT IMPLEMENTED) "
        "option."
    )
    assert "NOT IMPLEMENTED" in checklist_section, (
        "Section 13.2.1 must contain a 'NOT IMPLEMENTED' flag near "
        "the local option so an operator does not accidentally pick "
        "an unsupported path."
    )


def test_checklist_documents_celery_worker_beat_count(
    checklist_section: str,
):
    # Worker count and beat count are decisions the installer
    # CANNOT make without operator input on a PC test.
    assert "Number of uvicorn workers" in checklist_section, (
        "Section 13.2.1 must call out 'Number of uvicorn workers' "
        "so the operator knows to size them for the PC."
    )
    assert "Number of Celery worker children" in checklist_section, (
        "Section 13.2.1 must call out 'Number of Celery worker "
        "children' (separate from beat) so the operator can size "
        "Celery."
    )
    assert "Number of beat instances" in checklist_section, (
        "Section 13.2.1 must call out 'Number of beat instances' "
        "and clarify that exactly 1 is enforced via Redis SETNX."
    )
    assert "SETNX" in checklist_section or \
           "leader-lock" in checklist_section, (
        "Section 13.2.1 must explain WHY beat is always 1 (leader "
        "lock / SETNX) so an operator on a multi-host setup does "
        "not try to scale beat horizontally."
    )


def test_checklist_funnels_through_install_sh(checklist_section: str):
    # The idempotent installer (INSTALLATION-009 / 012 / 013) is
    # the supported path. The checklist MUST point at it instead
    # of describing manual .env construction in detail.
    assert "scripts/install.sh" in checklist_section, (
        "Section 13.2.2 must reference scripts/install.sh so the "
        "operator is funneled through the idempotent installer."
    )
    assert "scripts/install.sh --check" in checklist_section, (
        "Section 13.2.2 must reference the --check preflight mode "
        "(INSTALLATION-013) so the operator can validate the PC "
        "before running the install."
    )


def test_checklist_documents_rollback(checklist_section: str):
    assert "13.2.3 Rollback / troubleshooting" in checklist_section, (
        "Section 13.2.3 must exist with the rollback / "
        "troubleshooting heading. A first-PC test with no rollback "
        "leaves operators in a stuck state on first failure."
    )
    # The rollback must mention the four canonical recovery axes:
    # journalctl per service, schema reset, Redis FLUSHDB, .env
    # cleanup.
    expected = (
        ("journalctl", "Rollback section must reference journalctl "
         "for service log inspection."),
        ("DROP SCHEMA plenora CASCADE", "Rollback section must "
         "show the schema reset SQL so a stuck install can be "
         "wiped cleanly. The PC test caveat must be present."),
        ("FLUSHDB", "Rollback section must reference Redis FLUSHDB "
         "for broker / result backend cleanup."),
        ("rm -f", "Rollback section must show .env removal so a "
         "stuck install does not silently re-use bad config."),
    )
    for needle, message in expected:
        assert needle in checklist_section, message
    # Schema reset is destructive - the doc must call this out.
    assert "destructive" in checklist_section.lower(), (
        "Rollback section must explicitly flag the schema reset as "
        "destructive so an operator does not run it on a real DB."
    )


def test_checklist_documents_critical_gotchas(checklist_section: str):
    # The four gotchas that surface most often in real installs.
    expected = (
        ("Schema autocreate", "Section 13.2.4 must call out the "
         "schema autocreate gotcha (do NOT pre-create plenora)."),
        ("Single Redis URL", "Section 13.2.4 must call out the "
         "single Redis URL gotcha (no REDIS_URL; use "
         "CELERY_BROKER_URL)."),
        ("RESULT_DB_HARD_CAP_BYTES", "Section 13.2.4 must reference "
         "the BYTEA hard cap on STORAGE_POLICY=db so an operator "
         "knows when to switch policy."),
        ("Cleanroom CI is NOT a substitute",
         "Section 13.2.4 must call out that cleanroom CI green is "
         "NOT a substitute for a real PC install - this prevents "
         "the operator from skipping the manual smoke."),
    )
    for needle, message in expected:
        assert needle in checklist_section, message


def test_checklist_has_marco_command_sequence(checklist_section: str):
    # The literal copy/paste sequence is in 13.4. It must include
    # apt install, install.sh --check, and a foreground uvicorn
    # smoke (NOT systemd-first) so the operator can see boot gate
    # logs in real time on first run.
    assert "13.4 Marco's first-PC-test command sequence" in \
        checklist_section, (
        "Section 13.4 must exist with Marco's command sequence so "
        "the operator has a copy/paste path."
    )
    expected = (
        ("sudo apt install", "Marco command sequence must include "
         "the apt install line."),
        ("scripts/install.sh --check", "Marco command sequence "
         "must invoke --check before the install."),
        ("uvicorn app.main:app", "Marco command sequence must show "
         "the foreground uvicorn smoke (no systemd first run) so "
         "the operator can see boot logs in real time."),
        ("celery -A workers.celery_app worker", "Marco command "
         "sequence must show the foreground celery worker."),
        ("celery -A workers.celery_app beat", "Marco command "
         "sequence must show the foreground celery beat."),
        ("/health", "Marco command sequence must include the "
         "/health smoke."),
        ("/ready", "Marco command sequence must include the "
         "/ready smoke."),
    )
    for needle, message in expected:
        assert needle in checklist_section, message


def test_checklist_section_is_ascii_only(checklist_section: str):
    non_ascii = [
        (i, ch) for i, ch in enumerate(checklist_section)
        if ord(ch) > 127
    ]
    assert not non_ascii, (
        f"Section 13 must be ASCII-only (per scope rule). "
        f"Found {len(non_ascii)} non-ASCII char(s); first at offset "
        f"{non_ascii[0][0]}: U+{ord(non_ascii[0][1]):04X}."
    )
