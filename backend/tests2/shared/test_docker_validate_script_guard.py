"""Source-bound guards for the Docker validation pack.

BE-GO-READY-INSTALL-012-DOCKER-LINUX-VALIDATION-PACK.

Two contracts locked here:

  1. ``scripts/docker-validate.sh`` exists, advertises FIRST
     SCAFFOLD / NOT YET VALIDATED status, runs the eight
     canonical commands of the validation pack, and is
     **non-destructive** — it must NEVER call:
       - ``docker compose down`` (removes containers);
       - ``docker compose down -v`` (DESTRUCTIVE: removes
         volumes);
       - ``rm`` against ``.env.docker``;
       - ``docker volume rm`` against any ``plenora-*``
         volume;
       - ``docker system prune`` (any variant).

  2. ``docs/ops/install-docker.md`` carries the
     "Validation pack for the Linux PC test" section with
     the eight numbered expected-output blocks and the
     "FIRST SCAFFOLD, NOT YET VALIDATED" status line.

Source-bound: the test reads the script + doc as text and
asserts substring presence / absence. We do NOT execute the
script (no Docker on CI / Windows dev hosts). Same posture as
``test_docker_scaffold_guard.py``.

Outside scope:
  - the Dockerfile / docker-compose.yml / .env.docker.example
    contracts (locked by ``test_docker_scaffold_guard.py``);
  - the dry-run pack disclaimer (locked by
    ``test_install_marco_dry_run_doc_guard.py``);
  - the canonical 8-item smoke harness (locked by
    ``test_install_smoke_harness_doc_guard.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "docker-validate.sh"
_INSTALL_DOCKER = _REPO_ROOT / "docs" / "ops" / "install-docker.md"


# =====================================================================
# scripts/docker-validate.sh: existence + canonical contract
# =====================================================================


def test_validate_script_exists():
    assert _SCRIPT.is_file(), (
        "scripts/docker-validate.sh must exist (INSTALL-012). "
        "It is the convenience runner Marco invokes for the "
        "Linux PC validation pack."
    )


def test_validate_script_advertises_first_scaffold_status():
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "FIRST SCAFFOLD" in text, (
        "scripts/docker-validate.sh header must advertise the "
        "FIRST SCAFFOLD status. Without it operators may treat "
        "the script as a validated runner."
    )
    assert "NOT YET VALIDATED" in text, (
        "scripts/docker-validate.sh must keep the 'NOT YET "
        "VALIDATED' qualifier so a successful run is not "
        "interpreted as a Linux validation pass."
    )
    assert "BE-GO-READY-INSTALL-012-DOCKER-LINUX-VALIDATION-PACK" in text, (
        "scripts/docker-validate.sh must reference the "
        "BE-GO-READY-INSTALL-012 stream tag for traceability."
    )


def test_validate_script_supports_dry_run_and_help():
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "--dry-run" in text, (
        "scripts/docker-validate.sh must accept `--dry-run` so "
        "the eight commands can be previewed without executing "
        "them. Required for safe first-look on a real host."
    )
    assert "--help" in text, (
        "scripts/docker-validate.sh must accept `--help`."
    )


def test_validate_script_runs_eight_canonical_commands():
    text = _SCRIPT.read_text(encoding="utf-8")
    expected_commands = (
        "docker compose config",
        "docker compose build backend",
        "docker compose up -d postgres redis",
        "docker compose up -d --wait backend",
        "http://127.0.0.1:5004/health",
        "http://127.0.0.1:5004/ready",
        "docker compose up -d worker beat",
        "cleanroom.ping",
        "LOAD_FILE_MAX_BYTES",
        "docker compose stop",
    )
    for cmd in expected_commands:
        assert cmd in text, (
            f"scripts/docker-validate.sh must include the "
            f"canonical validation command/marker {cmd!r}. "
            "Each one corresponds to a numbered step of the "
            "validation pack in install-docker.md; missing one "
            "means the script and doc disagree."
        )


def _strip_shell_comments_and_heredocs(source: str) -> str:
    """Remove shell ``#``-prefixed lines and heredoc bodies so the
    non-destructive scan does not false-positive on phrases that
    appear only in the script header / usage docstring.

    The wrapper script's header explicitly enumerates the banned
    invocations as a contract reminder ("never runs `docker
    compose down`, `down -v`, ..."). Those mentions are
    documentation, not invocations, so we drop them before
    asserting.
    """
    out: list[str] = []
    in_heredoc = False
    heredoc_terminator: str | None = None
    for line in source.splitlines():
        stripped = line.lstrip()
        if in_heredoc:
            if heredoc_terminator is not None and stripped == heredoc_terminator:
                in_heredoc = False
                heredoc_terminator = None
            continue
        # Shell here-doc: `... <<'TAG'` or `... <<TAG`. Cheap
        # detection — sufficient because our script only uses
        # one heredoc shape (single-quoted USAGE).
        if "<<'" in line or '<<"' in line:
            tag_start = line.rfind("<<")
            quote = line[tag_start + 2]
            tag = line[tag_start + 3:].rstrip().rstrip(quote)
            in_heredoc = True
            heredoc_terminator = tag
            continue
        if stripped.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def test_validate_script_is_non_destructive():
    """Hard non-destructive contract. Adding any of the banned
    invocations re-introduces a footgun and breaks the contract
    documented in the script header.

    The scan ignores comments and heredoc bodies so the script
    header's enumeration of banned phrases (which is itself the
    contract reminder) does not false-positive.
    """
    code = _strip_shell_comments_and_heredocs(
        _SCRIPT.read_text(encoding="utf-8")
    )
    banned_patterns = (
        "docker compose down",
        "docker compose rm",
        "docker volume rm",
        "docker system prune",
        "rm -rf",
        "rm .env.docker",
        "rm -f .env.docker",
        "> .env.docker",
    )
    offenders = [p for p in banned_patterns if p in code]
    assert not offenders, (
        f"scripts/docker-validate.sh must remain non-destructive. "
        f"Banned invocations found: {offenders}. The teardown "
        "step is `docker compose stop` (preserves volumes). "
        "Volume / container removal and any operation that "
        "deletes or overwrites .env.docker is OUT OF SCOPE for "
        "this script — operators run those manually if they "
        "really want a destructive reset."
    )


def test_validate_script_does_not_clobber_env_docker():
    """The script must NOT regenerate or overwrite the
    `.env.docker` secrets file. Step 2 of install-docker.md is
    the only place `.env.docker` is created; the validation
    pack consumes it, never produces it."""
    text = _SCRIPT.read_text(encoding="utf-8")
    # Anything that writes to .env.docker is suspicious. The
    # banned-patterns test covers `rm`, `>`, etc; this test
    # adds the positive assertion that the script reads the
    # file (pre-flight existence check) without touching it.
    assert "ERROR: .env.docker not found" in text, (
        "scripts/docker-validate.sh must perform a pre-flight "
        "existence check on .env.docker (and exit 2 with a "
        "pointer to install-docker.md Step 2 if missing) — "
        "not silently regenerate the file."
    )


# =====================================================================
# BE-GO-READY-INSTALL-013-DOCKER-VALIDATE-LINUX-SAFETY
# =====================================================================
# Adds two structural guards that make the script safe to run on a
# real Linux PC without surprises:
#   - LF-only line endings (a CRLF copy fails with `bad interpreter:
#     /usr/bin/env bash^M`);
#   - explicit preflight diagnostics for missing `docker` binary AND
#     missing `docker compose` plugin (the v2 subcommand) BEFORE any
#     docker invocation runs.


def test_validate_script_uses_lf_line_endings():
    """The script MUST be checked out with LF line endings. A CRLF
    copy fails on Linux with `bad interpreter: /usr/bin/env bash^M`
    — the kernel reads the shebang line literally including the CR
    and tries to exec a non-existent interpreter.

    Locking the line-ending shape source-bound is the canonical
    cross-platform fix: the repo's `.gitattributes` pins
    `scripts/*.sh text eol=lf`, and this test asserts the on-disk
    file in the working tree carries the expected ending. Together
    they prevent a Windows contributor from re-saving with CRLF
    and pushing a script that breaks every Linux runner.
    """
    raw = _SCRIPT.read_bytes()
    crlf_count = raw.count(b"\r\n")
    assert crlf_count == 0, (
        f"scripts/docker-validate.sh contains {crlf_count} CRLF "
        "(`\\r\\n`) line ending(s). Linux executes the shebang "
        "line literally; a CRLF copy fails with 'bad interpreter: "
        "/usr/bin/env bash^M'. Re-save the file with LF endings "
        "and verify `.gitattributes` pins `scripts/*.sh "
        "text eol=lf`."
    )
    # Defense-in-depth: a lone CR (\r) without a following LF is
    # also a line-ending hazard (legacy classic-Mac style). The
    # script is plain text that should never contain CR at all.
    cr_count = raw.count(b"\r")
    assert cr_count == 0, (
        f"scripts/docker-validate.sh contains {cr_count} bare CR "
        "byte(s). The file must be pure LF (no CR, no CRLF) so "
        "the shebang line resolves cleanly on every Linux host."
    )


def test_gitattributes_pins_scripts_to_lf():
    """`.gitattributes` MUST contain a rule that pins `scripts/*.sh`
    to LF line endings. Without it a Windows clone with
    `core.autocrlf=true` can rewrite the file to CRLF on checkout
    even when the index version is LF — the script then fails on
    the first `bash` invocation with a `^M` interpreter error.

    This is the structural counterpart of the LF source-bound test
    above: the source-bound test catches a bad commit BEFORE merge,
    the .gitattributes rule keeps the working-copy LF AFTER merge.
    """
    gitattributes = _REPO_ROOT / ".gitattributes"
    assert gitattributes.is_file(), (
        ".gitattributes must exist at the repo root with an "
        "explicit `scripts/*.sh text eol=lf` rule (BE-GO-READY-"
        "INSTALL-013). Without it, Windows checkouts with "
        "core.autocrlf=true rewrite the validation script to "
        "CRLF and break it on Linux."
    )
    text = gitattributes.read_text(encoding="utf-8")
    # The rule shape `scripts/*.sh ... eol=lf` is the canonical form.
    # We accept either `text eol=lf` or just `eol=lf` after the
    # path glob.
    assert "scripts/*.sh" in text, (
        ".gitattributes must include a rule path-globbing "
        "`scripts/*.sh` (BE-GO-READY-INSTALL-013). Currently "
        "missing — the LF guarantee on the validation script "
        "is then implicit only and breaks under "
        "core.autocrlf=true."
    )
    assert "eol=lf" in text, (
        ".gitattributes must contain `eol=lf` for `scripts/*.sh` "
        "(BE-GO-READY-INSTALL-013). The bare path glob without "
        "the explicit `eol=lf` does not enforce line endings on "
        "Windows checkouts."
    )


def test_validate_script_preflights_docker_binary():
    """Preflight: missing `docker` binary on PATH must be detected
    BEFORE any docker invocation runs. Without the preflight, step
    1 (`docker compose config`) fails with the shell's generic
    `command not found` and the operator has no install pointer.

    Source-bound: the script must include both the `command -v
    docker` check AND the install-docs URL pointer in the error
    message."""
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "command -v docker" in text, (
        "scripts/docker-validate.sh must preflight the `docker` "
        "binary via `command -v docker` BEFORE any docker "
        "invocation. Without the preflight the operator hits "
        "`command not found` mid-pack with no install pointer."
    )
    assert "'docker' binary not found" in text, (
        "scripts/docker-validate.sh must surface a clear "
        "diagnostic (`'docker' binary not found on PATH.`) when "
        "the preflight detects a missing docker binary."
    )
    assert "docs.docker.com/engine/install" in text, (
        "scripts/docker-validate.sh missing-docker error message "
        "must point at the canonical install URL "
        "(docs.docker.com/engine/install) so the operator can "
        "fix the precondition without context-switching."
    )


def test_validate_script_preflights_docker_compose_plugin():
    """Preflight: `docker compose` v2 plugin must be detected
    BEFORE step 1. The validation pack uses the plugin shape
    (`docker compose ...` with a SPACE) NOT the legacy
    `docker-compose` standalone tool. Without the preflight, hosts
    with only the legacy tool installed fail step 1 with a
    confusing `unknown command "compose"`."""
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "docker compose version" in text, (
        "scripts/docker-validate.sh must preflight the "
        "`docker compose` plugin via `docker compose version` "
        "BEFORE any compose-prefixed step. The preflight is the "
        "diagnostic that distinguishes 'docker installed but "
        "compose plugin missing' from 'docker not installed at "
        "all'."
    )
    assert "'docker compose' plugin not available" in text, (
        "scripts/docker-validate.sh must surface a clear "
        "diagnostic when the compose plugin preflight fails. "
        "Operators on legacy hosts see 'unknown command \"compose\"' "
        "from the plugin failure path; the explicit preflight "
        "message points at the right install URL."
    )
    assert "docs.docker.com/compose/install" in text, (
        "scripts/docker-validate.sh missing-compose-plugin error "
        "message must point at the canonical install URL "
        "(docs.docker.com/compose/install) — the compose plugin "
        "install path is DIFFERENT from the docker engine "
        "install path on most distros."
    )


def test_validate_script_preflights_run_in_correct_order():
    """Preflight ordering: the four preconditions MUST be checked
    BEFORE the `run_step` invocations. Without the ordering a
    failed precondition would surface only AFTER step 1's docker
    invocation, defeating the diagnostic value of the preflight.

    Source-bound: assert the four preflight ERROR markers all
    appear textually BEFORE the first `run_step 1` call.
    """
    text = _SCRIPT.read_text(encoding="utf-8")
    preflight_markers = (
        "ERROR: docker-compose.yml not found",
        "ERROR: 'docker' binary not found",
        "ERROR: 'docker compose' plugin not available",
        "ERROR: .env.docker not found",
    )
    first_step_marker = "run_step 1 "
    first_step_pos = text.find(first_step_marker)
    assert first_step_pos != -1, (
        "scripts/docker-validate.sh must invoke `run_step 1` for "
        "the canonical first command (locked by "
        "test_validate_script_runs_eight_canonical_commands)."
    )
    for marker in preflight_markers:
        pos = text.find(marker)
        assert pos != -1, (
            f"scripts/docker-validate.sh must contain preflight "
            f"diagnostic {marker!r} (BE-GO-READY-INSTALL-013)."
        )
        assert pos < first_step_pos, (
            f"scripts/docker-validate.sh preflight diagnostic "
            f"{marker!r} must appear textually BEFORE the first "
            "`run_step 1` invocation. A preflight that fires AFTER "
            "step 1 has no diagnostic value -- the operator has "
            "already seen the cryptic docker-side failure."
        )


# =====================================================================
# install-docker.md: validation pack section
# =====================================================================


def test_install_docker_doc_has_validation_pack_section():
    text = _INSTALL_DOCKER.read_text(encoding="utf-8")
    assert "Validation pack for the Linux PC test" in text, (
        "install-docker.md must include a 'Validation pack for "
        "the Linux PC test' section (INSTALL-012). Without it "
        "Marco has no canonical copy/paste sequence to run on "
        "his Linux PC."
    )
    assert "BE-GO-READY-INSTALL-012-DOCKER-LINUX-VALIDATION-PACK" in text, (
        "install-docker.md validation pack section must "
        "reference the BE-GO-READY-INSTALL-012 stream tag."
    )
    assert "FIRST SCAFFOLD, NOT YET VALIDATED" in text, (
        "install-docker.md validation pack section must keep "
        "the 'FIRST SCAFFOLD, NOT YET VALIDATED' status header. "
        "Removing it lets the doc be mistaken for a validated "
        "path."
    )


def test_install_docker_doc_references_validation_script():
    text = _INSTALL_DOCKER.read_text(encoding="utf-8")
    assert "scripts/docker-validate.sh" in text, (
        "install-docker.md must reference the convenience "
        "wrapper `scripts/docker-validate.sh`. The doc and the "
        "script are paired artifacts; missing the pointer means "
        "operators duplicate the eight commands by hand."
    )
    assert "--dry-run" in text, (
        "install-docker.md validation pack section must show "
        "the `--dry-run` invocation as the recommended first "
        "look before executing the eight commands."
    )


def test_install_docker_doc_lists_expected_outputs_for_each_step():
    """Every numbered validation step must have an expected
    output / failure paste guidance attached. The shape (a
    numbered list 1..8 with concrete shapes) is the contract;
    we assert each numbered marker is present alongside an
    expected-output cue."""
    text = _INSTALL_DOCKER.read_text(encoding="utf-8")
    # The numbered markdown list `1. **`docker compose ...`**`
    # uses bold backtick'd commands.
    expected_markers = (
        "1. **`docker compose config`**",
        "2. **`docker compose build backend`**",
        "3. **`docker compose up -d --wait backend`**",
        "4. **`curl -fsS http://127.0.0.1:5004/health`**",
        "5. **`docker compose up -d worker beat`**",
        "6. **`cleanroom.ping`**",
        "7. **memory caps visibility**",
        "8. **`docker compose stop`**",
    )
    for marker in expected_markers:
        assert marker in text, (
            f"install-docker.md validation pack must include "
            f"numbered expected-output entry {marker!r}. Each "
            "numbered entry pins the shape Marco must paste "
            "back; dropping one creates an unverifiable step."
        )


def test_install_docker_doc_documents_paste_back_artifacts():
    text = _INSTALL_DOCKER.read_text(encoding="utf-8")
    # Marco's report-back must include host metadata so the
    # validation report has a complete fingerprint.
    for marker in (
        "uname -a",
        "docker --version",
        "docker compose version",
        "/etc/os-release",
    ):
        assert marker in text, (
            f"install-docker.md validation pack must request "
            f"host metadata {marker!r} as part of the paste-"
            "back artifacts. Without the host fingerprint the "
            "validation report is not reproducible."
        )


def test_install_docker_doc_documents_script_non_destructive_contract():
    text = _INSTALL_DOCKER.read_text(encoding="utf-8")
    # The doc must call out the script's non-destructive
    # contract so operators know what the wrapper avoids.
    assert "non-destructive" in text.lower(), (
        "install-docker.md validation pack section must "
        "describe the script's non-destructive contract."
    )
    assert "docker compose down" in text, (
        "install-docker.md must explicitly call out that "
        "`docker compose down` (and its `-v` variant) are NOT "
        "run by the validation pack."
    )


# =====================================================================
# BE-GO-READY-INSTALL-019: FE gap warnings in script output
# =====================================================================


def test_validate_script_defines_fe_gap_report_function():
    """BE-GO-READY-INSTALL-019: `report_fe_gap_status` must be defined
    in the script to make FE/CI gap truth visible to the operator at
    pack-start instead of requiring them to read the docs separately.

    The three gaps it reports match exactly the three negative locks in
    `test_install_docker_fe_gap_guard.py`."""
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "report_fe_gap_status() {" in text, (
        "scripts/docker-validate.sh must define `report_fe_gap_status` "
        "(BE-GO-READY-INSTALL-019). The function reports the three "
        "FE/CI artifacts missing from the current scaffold so the "
        "operator sees the gaps upfront without reading the docs."
    )


def test_validate_script_fe_gap_report_has_canonical_warn_messages():
    """The three canonical WARN messages must exist in the script.

    Each string corresponds to one of the three negative locks in
    `test_install_docker_fe_gap_guard.py`. Dropping one silently hides
    that gap class from the operator-facing report."""
    text = _SCRIPT.read_text(encoding="utf-8")
    warn_messages = (
        "FE image NOT BUILT",
        "full-stack Docker NOT AVAILABLE",
        "Docker CI NOT WIRED",
    )
    for msg in warn_messages:
        assert msg in text, (
            f"scripts/docker-validate.sh FE gap report must include "
            f"WARN message {msg!r} (BE-GO-READY-INSTALL-019). Missing "
            "one means a gap class is hidden from the operator-facing "
            "transcript."
        )


def test_validate_script_ci_gap_check_looks_for_run_steps_not_comments():
    """The CI detector must not treat a workflow comment that mentions
    `docker compose` as a wired Docker CI lane. It should look for a
    workflow `run:` step that invokes docker compose."""
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "^[[:space:]]*-[[:space:]]*run:" in text, (
        "scripts/docker-validate.sh must detect Docker CI from "
        "workflow `run:` steps, not arbitrary comments/doc strings."
    )
    assert "grep -rlF \"docker compose\" .github/workflows/" not in text, (
        "A plain recursive grep would count comments mentioning "
        "`docker compose` as a real CI lane. Keep the run-step "
        "regex instead."
    )


def test_validate_script_calls_fe_gap_report_before_run_step_1():
    """report_fe_gap_status must be CALLED before run_step 1 so the
    operator sees the FE gap summary before the pack starts executing.

    A call that appears only after step 1 (or not at all) means the
    gaps are invisible until after the potentially long build begins."""
    text = _SCRIPT.read_text(encoding="utf-8")
    call_pos = text.find("\nreport_fe_gap_status\n")
    step1_pos = text.find("run_step 1 ")
    assert call_pos != -1, (
        "scripts/docker-validate.sh must invoke `report_fe_gap_status` "
        "as a standalone call (not just define it) "
        "(BE-GO-READY-INSTALL-019)."
    )
    assert step1_pos != -1, (
        "run_step 1 must exist in scripts/docker-validate.sh "
        "(locked by test_validate_script_runs_eight_canonical_commands)."
    )
    assert call_pos < step1_pos, (
        "report_fe_gap_status must be called BEFORE `run_step 1` "
        "(BE-GO-READY-INSTALL-019). A late call means the FE gap "
        "summary appears only after the build has started."
    )
