"""
Architectural lock for the Linux PC execution pack.

BE-GO-READY-INSTALL-010-LINUX-EXECUTION-PACK.
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
_INSTALL_DOCKER = _REPO_ROOT / "docs" / "ops" / "install-docker.md"


_ROLLBACK_HEADER = "### 4.8 Non-destructive rollback (foreground smoke)"
_DOCKER_STATUS_HEADER = "## Status today (command-first, executable scaffold)"


@pytest.fixture(scope="module")
def dry_run_text() -> str:
    return _DRY_RUN.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def docker_text() -> str:
    return _INSTALL_DOCKER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rollback_section(dry_run_text: str) -> str:
    if _ROLLBACK_HEADER not in dry_run_text:
        return ""
    start = dry_run_text.index(_ROLLBACK_HEADER)
    # End at the section-5 separator.
    end_marker = "\n## 5. Clean-install smoke harness"
    end = dry_run_text.find(end_marker, start)
    if end == -1:
        end = len(dry_run_text)
    return dry_run_text[start:end]


@pytest.fixture(scope="module")
def docker_status_section(docker_text: str) -> str:
    if _DOCKER_STATUS_HEADER not in docker_text:
        return ""
    start = docker_text.index(_DOCKER_STATUS_HEADER)
    end_marker = "\n## Target shape (specification only)"
    end = docker_text.find(end_marker, start)
    if end == -1:
        end = len(docker_text)
    return docker_text[start:end]


# Dry-run pack section 4.8


def test_rollback_section_exists(rollback_section: str):
    assert rollback_section, (
        "docs/ops/install-marco-dry-run.md must contain section "
        f"4.8 with header {_ROLLBACK_HEADER!r}. The "
        "non-destructive rollback is the brief's explicit ask "
        "for INSTALL-010; section 4.7 is destructive-only and "
        "is the wrong tool when an operator iterates on a "
        "transient issue."
    )
    assert "BE-GO-READY-INSTALL-010-LINUX-EXECUTION-PACK" \
        in rollback_section, (
        "Section 4.8 must keep the BE-GO-READY-INSTALL-010 "
        "stream tag so the audit trail back to the brief is "
        "preserved."
    )


def test_rollback_uses_signal_first_not_kill_dash_9_first(
    rollback_section: str,
):
    # The rollback MUST start with a graceful signal. Going
    # straight to SIGKILL would leave Celery tasks ack'd-but-
    # not-completed, which is NOT idempotent on rerun.
    assert "pkill -INT" in rollback_section, (
        "Section 4.8 must use `pkill -INT` (SIGINT) as the first "
        "step. SIGINT triggers the standard FastAPI / Celery "
        "graceful shutdown path. Switching to `kill -9` first "
        "would leave broker state inconsistent on rerun."
    )
    # SIGKILL appears AFTER SIGINT as the escalation only.
    sigkill_idx = rollback_section.find("pkill -KILL")
    sigint_idx = rollback_section.find("pkill -INT")
    assert sigint_idx != -1
    assert sigkill_idx == -1 or sigkill_idx > sigint_idx, (
        "If section 4.8 references `pkill -KILL`, it MUST "
        "appear AFTER the `pkill -INT` step (escalation only). "
        "Reordering would skip the graceful shutdown phase."
    )


def test_rollback_does_not_destroy_state(rollback_section: str):
    # The non-destructive rollback MUST NOT include the
    # destructive command FORMS. The destructive total reset
    # has its own section (4.7). We check command-line shapes
    # (the actual subprocess invocations) rather than bare
    # phrases, because section 4.8's negative-list intentionally
    # MENTIONS the destructive primitives ("does NOT DROP
    # SCHEMA / FLUSHDB").
    forbidden_command_shapes = (
        '-c "DROP SCHEMA',
        "redis-cli FLUSHDB",
        "redis-cli -u",  # destructive section uses url+FLUSHDB
        "rm -f  ~/plenora-etl/backend/.env",
        "rm -rf ~/plenora-etl/backend/data",
        "systemctl disable plenora",
    )
    for needle in forbidden_command_shapes:
        assert needle not in rollback_section, (
            f"Section 4.8 (non-destructive rollback) must NOT "
            f"contain the destructive command shape {needle!r}. "
            "That command belongs to section 4.7 (Total reset "
            "DESTRUCTIVE). Mixing them defeats the purpose of "
            "having a non-destructive path."
        )


def test_rollback_documents_what_it_skips(rollback_section: str):
    # The section must explicitly enumerate what it leaves
    # alone. Without this, an operator may not realise the
    # migrated schema / Redis state / .env are preserved.
    expected_phrases = (
        ("DROP SCHEMA",
         "Section 4.8 must mention 'DROP SCHEMA' in the "
         "negative-list (does NOT do) so the operator knows "
         "the schema is preserved. The phrase appears in a "
         "'does NOT' bullet, not as a command."),
        ("FLUSHDB",
         "Section 4.8 must mention 'FLUSHDB' in the negative-"
         "list so the operator knows Redis state is preserved."),
        ("backend/.env",
         "Section 4.8 must mention `backend/.env` in the "
         "negative-list so the operator knows the env file is "
         "preserved."),
    )
    for needle, message in expected_phrases:
        assert needle in rollback_section, message
    # Cross-link to the destructive sibling section.
    assert "section 4.7" in rollback_section, (
        "Section 4.8 must cross-link to section 4.7 for the "
        "destructive total-reset path. Without the cross-link "
        "an operator who actually wants the destructive path "
        "may apply 4.8 repeatedly hoping it eventually wipes "
        "state."
    )


# install-docker.md "Status today" section


def test_docker_status_section_exists(docker_status_section: str):
    assert docker_status_section, (
        "docs/ops/install-docker.md must contain a "
        f"{_DOCKER_STATUS_HEADER!r} section. INSTALL-010 added "
        "this command-first table so an operator with Docker "
        "pre-installed has a one-screen answer to 'what do I do "
        "TODAY' instead of having to reverse-engineer the "
        "single-machine path."
    )
    assert "BE-GO-READY-INSTALL-010-LINUX-EXECUTION-PACK" \
        in docker_status_section, (
        "The 'Status today' section must keep the "
        "BE-GO-READY-INSTALL-010 stream tag."
    )


def test_docker_status_section_marks_validation_pending(
    docker_status_section: str,
):
    # INSTALL-011 supersedes the original "NOT IMPLEMENTED"
    # honesty markers with "NO (scaffold)" validation markers.
    # The table now contains executable Docker commands per row;
    # the load-bearing honesty signal is the per-row "NO
    # (scaffold)" cell that says the command has not been
    # validated on a real Linux PC yet.
    no_scaffold_count = docker_status_section.count("NO (scaffold)")
    assert no_scaffold_count >= 8, (
        f"The 'Status today' section must contain 'NO "
        f"(scaffold)' at least 8 times (one per harness row). "
        f"Found {no_scaffold_count}. Each row must mark the "
        "Docker side as 'NO (scaffold)' for the 'Validated on "
        "Linux PC' column so an operator does not mistake an "
        "executable scaffold command for a validated path."
    )
    # The scaffold flag must remain visible alongside.
    assert "FIRST SCAFFOLD" in docker_status_section or \
           "scaffold" in docker_status_section.lower(), (
        "The 'Status today' section must reference the FIRST "
        "SCAFFOLD nature of the Docker path so the validation "
        "gap is unmistakable."
    )


def test_docker_status_section_lists_eight_harness_items(
    docker_status_section: str,
):
    # The eight items mirror the canonical smoke harness in
    # install-marco-dry-run.md section 5.1. Each must appear so
    # the cross-document mapping is complete.
    expected_items = (
        "prereqs",
        "env decisions",
        "install",
        "smoke",
        "health/ready",
        "Celery check",
        "memory caps visibility",
        "non-destructive rollback",
    )
    for item in expected_items:
        assert item in docker_status_section, (
            f"The 'Status today' section must include the "
            f"harness item {item!r} to match the canonical "
            "8-item harness in install-marco-dry-run.md "
            "section 5.1."
        )


def test_docker_status_section_points_back_to_dry_run_pack(
    docker_status_section: str,
):
    # The single-machine commands live in the dry-run pack;
    # the Docker status section MUST point at it so the
    # operator does not fabricate commands.
    assert "install-marco-dry-run.md" in docker_status_section, (
        "The 'Status today' section must cross-link to "
        "install-marco-dry-run.md for the executable single-"
        "machine commands. Without the link, an operator with "
        "Docker pre-installed has to grep the docs tree."
    )
    # The canonical Celery primitive name MUST appear so the
    # operator can find it without scanning the dry-run pack.
    assert "cleanroom.ping" in docker_status_section, (
        "The 'Status today' section row #6 (Celery check) must "
        "reference the canonical `cleanroom.ping` primitive so "
        "the operator can run the same smoke locally as the "
        "cleanroom CI."
    )


# ASCII-only


def test_rollback_section_is_ascii_only(rollback_section: str):
    non_ascii = [
        (i, ch) for i, ch in enumerate(rollback_section)
        if ord(ch) > 127
    ]
    assert not non_ascii, (
        f"Section 4.8 must be ASCII-only. Found "
        f"{len(non_ascii)} non-ASCII char(s); first at offset "
        f"{non_ascii[0][0]}: U+{ord(non_ascii[0][1]):04X}."
    )


def test_docker_status_section_is_ascii_only(
    docker_status_section: str,
):
    non_ascii = [
        (i, ch) for i, ch in enumerate(docker_status_section)
        if ord(ch) > 127
    ]
    assert not non_ascii, (
        f"The 'Status today' section of install-docker.md must "
        f"be ASCII-only. Found {len(non_ascii)} non-ASCII "
        f"char(s); first at offset {non_ascii[0][0]}: "
        f"U+{ord(non_ascii[0][1]):04X}."
    )
