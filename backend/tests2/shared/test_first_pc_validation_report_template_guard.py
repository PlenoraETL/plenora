"""Source-bound guard for the first-PC validation report template.

BE-GO-READY-INSTALL-016-FIRST-PC-COMMAND-GUARD.

Locks the load-bearing command shapes inside
`docs/ops/first-pc-validation-report-template.md` so a future
edit cannot silently drop one of the six commands the brief
named as the operator-handoff contract:

  1. commit hash + working-tree status (`git rev-parse` + `git status`);
  2. `bash scripts/install.sh --check`;
  3. non-interactive install with the full 7-var `PLENORA_INSTALL_*`
     env shape (no `... \\` truncation);
  4. `/health` and `/ready` curl probes;
  5. `cleanroom.ping` round-trip with `AsyncResult.get(timeout=...)`;
  6. Docker `bash scripts/docker-validate.sh --dry-run` AND a real
     run path.

Honesty contract: the Docker section must keep the FIRST SCAFFOLD
/ NOT YET VALIDATED markers; the install MUST NOT be claimed as
validated by this template (only the report Marco pastes back can
flip a path to "validated").

Stays source-bound (zero app imports) per the dry-run / smoke
harness guard family posture (mirror of
`test_install_smoke_harness_doc_guard.py` and
`test_install_execution_pack_doc_guard.py`).

Outside scope:
  - the dry-run pack itself (locked by
    `test_install_marco_dry_run_doc_guard.py`);
  - the smoke harness section 5 (locked by
    `test_install_smoke_harness_doc_guard.py`);
  - the Docker scaffold scripts (locked by
    `test_docker_scaffold_guard.py` /
    `test_docker_validate_script_guard.py`).
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_TEMPLATE = (
    _REPO_ROOT / "docs" / "ops" / "first-pc-validation-report-template.md"
)


@pytest.fixture(scope="module")
def template_text() -> str:
    return _TEMPLATE.read_text(encoding="utf-8")


# 1. Load-bearing commands - present and not truncated


_REQUIRED_COMMAND_SHAPES: tuple[tuple[str, str], ...] = (
    (
        "git rev-parse --short HEAD",
        "Section 1 must include `git rev-parse --short HEAD` so "
        "the operator pastes the exact commit. Without it, a "
        "validation report cannot be tied back to a build.",
    ),
    (
        "git status --short",
        "Section 1 must include `git status --short` so the "
        "operator confirms a clean working tree before the run.",
    ),
    (
        "bash scripts/install.sh --check",
        "Section 2 must include `bash scripts/install.sh --check` "
        "as the preflight handoff -- the same command the brief "
        "names. Drift here means the operator runs a different "
        "preflight than the dry-run pack documents.",
    ),
    (
        "bash scripts/install.sh --non-interactive",
        "Section 2 must include `bash scripts/install.sh "
        "--non-interactive` for the guided install step.",
    ),
    (
        "curl -fsS http://127.0.0.1:5004/health",
        "Section 2 must include the `/health` curl probe with "
        "`-fsS` and the canonical port (5004). The install pack "
        "guard tests assume the same shape; drift would split the "
        "operator-pasted log from the doc-checked one.",
    ),
    (
        "curl -fsS http://127.0.0.1:5004/ready",
        "Section 2 must include the `/ready` curl probe with the "
        "same canonical shape.",
    ),
    (
        "cleanroom.ping",
        "The template must reference the canonical `cleanroom.ping` "
        "Celery task name -- the same primitive locked by the "
        "smoke harness guard. A renamed task here would let "
        "Marco run a different smoke than the cleanroom CI lane.",
    ),
    (
        "ar.get(timeout=30)",
        "The cleanroom.ping snippet must use the bounded "
        "`AsyncResult.get(timeout=30)` primitive (NOT a sleep "
        "loop, NOT an unbounded .get()). Same pattern as the "
        "cleanroom CI workflow and the dry-run pack section 5.3.",
    ),
    (
        "bash scripts/docker-validate.sh --dry-run",
        "Section 3 must include `bash scripts/docker-validate.sh "
        "--dry-run` BEFORE the real Docker run. The dry-run is the "
        "preflight that confirms the validation pack itself parses "
        "on this host without spending build time.",
    ),
    (
        "docker compose build backend",
        "Section 3 must include the real `docker compose build "
        "backend` command for the Docker scaffold validation. The "
        "dry-run does not actually build; this is the real-run "
        "command the brief asks Marco to paste back.",
    ),
)


def test_required_command_shapes_present(template_text: str):
    missing = [
        (needle, msg)
        for needle, msg in _REQUIRED_COMMAND_SHAPES
        if needle not in template_text
    ]
    assert not missing, (
        "Load-bearing command shapes missing from "
        "first-pc-validation-report-template.md:\n"
        + "\n".join(f"  - {needle!r}: {msg}" for needle, msg in missing)
    )


# 2. Non-interactive env shape - full 7 vars, no truncation


_REQUIRED_PLENORA_INSTALL_VARS: tuple[str, ...] = (
    "PLENORA_INSTALL_ENVIRONMENT=development",
    "PLENORA_INSTALL_DATABASE_URL=",
    "PLENORA_INSTALL_DB_SCHEMA=plenora",
    "PLENORA_INSTALL_REDIS_URL=",
    "PLENORA_INSTALL_STORAGE_POLICY=db",
    "PLENORA_INSTALL_BACKUP_BUCKET=",
    "PLENORA_INSTALL_BACKEND_PORT=5004",
)


def test_non_interactive_env_shape_is_full(template_text: str):
    """Section 2.3 must spell out all 7 `PLENORA_INSTALL_*` env
    vars verbatim. A truncated `... \\` shape lets the operator
    omit a required var, which makes the script prompt and hang.
    """
    for needle in _REQUIRED_PLENORA_INSTALL_VARS:
        assert needle in template_text, (
            f"Section 2.3 must include `{needle}` (the full "
            f"7-var env shape; no `... \\` truncation). Missing "
            "vars cause `scripts/install.sh --non-interactive` "
            "to hang on a prompt."
        )


def test_no_truncated_env_shape(template_text: str):
    """The literal `  ... \\` placeholder used during drafting
    must NOT appear in the locked template. Its presence here is
    a regression -- it means the env shape was elided again."""
    forbidden = "  ... \\\n  bash scripts/install.sh"
    assert forbidden not in template_text, (
        "first-pc-validation-report-template.md has a truncated "
        "env block (`  ... \\` followed by the install script "
        "invocation). Spell out every `PLENORA_INSTALL_*` var "
        "explicitly so the operator does not omit one and hang "
        "on a prompt."
    )


# 3. Docker honesty markers - FIRST SCAFFOLD / NOT YET VALIDATED


def test_docker_section_keeps_first_scaffold_markers(template_text: str):
    """The Docker section must keep both the FIRST SCAFFOLD and
    the NOT YET VALIDATED markers so a casual reader cannot
    misread the template as a validation claim. The brief is
    explicit: 'Do not claim the install is validated.'"""
    assert "FIRST SCAFFOLD" in template_text, (
        "first-pc-validation-report-template.md must keep the "
        "literal `FIRST SCAFFOLD` honesty marker for the Docker "
        "validation path."
    )
    assert "NOT YET VALIDATED" in template_text, (
        "first-pc-validation-report-template.md must keep the "
        "literal `NOT YET VALIDATED` honesty marker. The brief "
        "explicitly says: do not claim the install is validated."
    )


def test_template_does_not_claim_install_is_validated(template_text: str):
    """Negative lock: phrases that would imply the install is
    pre-validated must NOT appear unprefixed. The template is the
    *output* template; it cannot itself be the validation."""
    forbidden_phrases = (
        # Bare claim that the install is validated. The template
        # MUST stay agnostic; only the filled-in report Marco
        # pastes back can flip a path to validated.
        "install is validated",
        "Plenora-ETL is validated",
        "production-ready Linux install",
    )
    for needle in forbidden_phrases:
        assert needle not in template_text, (
            f"Forbidden validation claim {needle!r} found in the "
            "report template. The template is the form Marco "
            "fills out; it is NEVER itself a validation. Use a "
            "phrase that defers the claim to the filled-in "
            "report (see section 'What \"validated install\" "
            "means' at the bottom of the template)."
        )


# 4. ASCII-only (per the install-doc family scope rule)


def test_template_is_ascii_only(template_text: str):
    non_ascii = [
        (i, ch) for i, ch in enumerate(template_text) if ord(ch) > 127
    ]
    assert not non_ascii, (
        "first-pc-validation-report-template.md must be "
        "ASCII-only (per BE-GO-READY-INSTALL family scope rule). "
        f"Found {len(non_ascii)} non-ASCII char(s); first at "
        f"offset {non_ascii[0][0]}: U+{ord(non_ascii[0][1]):04X}."
    )
