"""
Source-bound guards for the Docker validation pack.

BE-GO-READY-INSTALL-012-DOCKER-LINUX-VALIDATION-PACK.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "docker-validate.sh"
_INSTALL_DOCKER = _REPO_ROOT / "docs" / "ops" / "install-docker.md"


@pytest.fixture(scope="module")
def validate_script_text() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def install_docker_text() -> str:
    return _INSTALL_DOCKER.read_text(encoding="utf-8")


def _assert_contains_all(text: str, markers: tuple[str, ...], message: str) -> None:
    for marker in markers:
        assert marker in text, message.format(marker=marker)


# scripts/docker-validate.sh: existence + canonical contract


def test_validate_script_exists():
    assert _SCRIPT.is_file(), (
        "scripts/docker-validate.sh must exist (INSTALL-012). "
        "It is the convenience runner Marco invokes for the "
        "Linux PC validation pack."
    )


@pytest.mark.parametrize(
    ("markers", "message"),
    (
        (
            (
                "FIRST SCAFFOLD",
                "NOT YET VALIDATED",
                "BE-GO-READY-INSTALL-012-DOCKER-LINUX-VALIDATION-PACK",
            ),
            "scripts/docker-validate.sh header missing {marker!r}; keep "
            "the scaffold status and stream tag visible so operators do "
            "not mistake the runner for a validated Linux pass.",
        ),
        (
            ("--dry-run", "--help"),
            "scripts/docker-validate.sh must accept {marker!r}; dry-run "
            "and help keep first-look execution safe on a real host.",
        ),
    ),
)
def test_validate_script_header_and_cli_contract(
    validate_script_text: str,
    markers: tuple[str, ...],
    message: str,
):
    _assert_contains_all(validate_script_text, markers, message)


def test_validate_script_runs_eight_canonical_commands(validate_script_text: str):
    expected_commands = (
        "docker compose config",
        "docker compose build backend frontend",
        "docker compose up -d postgres rabbitmq",
        "docker compose up -d --wait backend",
        "http://127.0.0.1:5004/health",
        "http://127.0.0.1:5004/ready",
        "information_schema.schemata",
        "schema_name = 'plenora'",
        "to_regclass('plenora.schema_migrations') IS NOT NULL",
        "SELECT count(*) FROM plenora.schema_migrations",
        "docker compose up -d worker beat",
        "cleanroom.ping",
        "LOAD_FILE_MAX_BYTES",
        "docker compose stop",
    )
    _assert_contains_all(
        validate_script_text,
        expected_commands,
        "scripts/docker-validate.sh must include canonical validation "
        "command/marker {marker!r}. Each one corresponds to a numbered "
        "step of the validation pack in install-docker.md.",
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


def test_validate_script_is_non_destructive(validate_script_text: str):
    """Hard non-destructive contract. Adding any of the banned
    invocations re-introduces a footgun and breaks the contract
    documented in the script header.

    The scan ignores comments and heredoc bodies so the script
    header's enumeration of banned phrases (which is itself the
    contract reminder) does not false-positive.
    """
    code = _strip_shell_comments_and_heredocs(validate_script_text)
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


def test_validate_script_does_not_clobber_env_docker(validate_script_text: str):
    """The script must NOT regenerate or overwrite the
    `.env.docker` secrets file. Step 2 of install-docker.md is
    the only place `.env.docker` is created; the validation
    pack consumes it, never produces it."""
    # Anything that writes to .env.docker is suspicious. The
    # banned-patterns test covers `rm`, `>`, etc; this test
    # adds the positive assertion that the script reads the
    # file (pre-flight existence check) without touching it.
    assert "ERROR: .env.docker not found" in validate_script_text, (
        "scripts/docker-validate.sh must perform a pre-flight "
        "existence check on .env.docker (and exit 2 with a "
        "pointer to install-docker.md Step 2 if missing) — "
        "not silently regenerate the file."
    )


def test_validate_script_has_read_only_schema_postcondition_after_ready(
    validate_script_text: str,
):
    """The Docker validation pack must assert DB bootstrap directly after
    `/ready`, without relying only on the HTTP readiness aggregation."""
    required_markers = (
        "run_schema_postcondition() {",
        "Postgres schema + migrations postcondition",
        "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'plenora');",
        "SELECT to_regclass('plenora.schema_migrations') IS NOT NULL;",
        "SELECT count(*) FROM plenora.schema_migrations;",
        "schema_migrations_rows=",
    )
    _assert_contains_all(
        validate_script_text,
        required_markers,
        "scripts/docker-validate.sh must include read-only schema "
        "postcondition marker {marker!r}. The Docker cleanroom gate needs "
        "direct proof that schema `plenora` exists and migrations have "
        "applied rows after `/ready`.",
    )

    ready_pos = validate_script_text.find('run_step 4b "GET /ready"')
    schema_call_pos = validate_script_text.find("\nrun_schema_postcondition\n")
    worker_pos = validate_script_text.find('run_step 5 "up worker + beat"')
    assert ready_pos != -1, "The `/ready` validation step must exist."
    assert schema_call_pos != -1, (
        "`run_schema_postcondition` must be called as a standalone step."
    )
    assert worker_pos != -1, "The worker startup step must exist."
    assert ready_pos < schema_call_pos < worker_pos, (
        "The schema postcondition must run after `/ready` and before "
        "worker/beat startup, so backend bootstrap/migrations are proven "
        "before the Dramatiq path is exercised."
    )


def test_validate_script_schema_postcondition_is_select_only(
    validate_script_text: str,
):
    """The schema postcondition is an observer, not a bootstrapper."""
    start = validate_script_text.find("run_schema_postcondition() {")
    # Cerca la chiusura della funzione: `\n}\n` seguito da una riga
    # vuota e da un commento bash (qualunque sia il separatore
    # successivo, non solo l'antico `# ----`).
    end = validate_script_text.find("\n}\n\n# ", start)
    assert start != -1 and end != -1, (
        "Could not isolate `run_schema_postcondition`; keep the helper "
        "standalone so its read-only contract remains auditable."
    )
    helper = validate_script_text[start:end]
    for marker in ("CREATE ", "INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER "):
        assert marker not in helper.upper(), (
            "The schema postcondition must stay read-only. Found a "
            f"write/DDL marker in the helper: {marker!r}."
        )
    assert helper.count(" psql -U plenora -d plenora -tAc ") >= 3, (
        "The schema postcondition should use explicit psql scalar SELECT "
        "commands for schema existence, table existence, and applied-row "
        "count."
    )


# BE-GO-READY-INSTALL-013-DOCKER-VALIDATE-LINUX-SAFETY
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


@pytest.mark.parametrize(
    "markers",
    (
        (
            "command -v docker",
            "'docker' binary not found",
            "docs.docker.com/engine/install",
        ),
        (
            "docker compose version",
            "'docker compose' plugin not available",
            "docs.docker.com/compose/install",
        ),
    ),
)
def test_validate_script_preflights_docker_dependencies(
    validate_script_text: str,
    markers: tuple[str, ...],
):
    _assert_contains_all(
        validate_script_text,
        markers,
        "scripts/docker-validate.sh missing Docker dependency preflight "
        "marker {marker!r}. Missing it sends operators into step 1 with "
        "a generic docker-side failure instead of an install pointer.",
    )


def test_validate_script_preflights_run_in_correct_order(validate_script_text: str):
    """Preflight ordering: the four preconditions MUST be checked
    BEFORE the `run_step` invocations. Without the ordering a
    failed precondition would surface only AFTER step 1's docker
    invocation, defeating the diagnostic value of the preflight.

    Source-bound: assert the four preflight ERROR markers all
    appear textually BEFORE the first `run_step 1` call.
    """
    text = validate_script_text
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


# install-docker.md: validation pack section


@pytest.mark.parametrize(
    ("markers", "message"),
    (
        (
            (
                "Validation pack for the Linux PC test",
                "BE-GO-READY-INSTALL-012-DOCKER-LINUX-VALIDATION-PACK",
                "FIRST SCAFFOLD, NOT YET VALIDATED",
            ),
            "install-docker.md validation pack section missing {marker!r}; "
            "Marco needs the canonical status and traceability marker.",
        ),
        (
            ("scripts/docker-validate.sh", "--dry-run"),
            "install-docker.md must document {marker!r}; the doc and "
            "wrapper are paired artifacts, and dry-run is the safe first look.",
        ),
    ),
)
def test_install_docker_doc_textual_contracts(
    install_docker_text: str,
    markers: tuple[str, ...],
    message: str,
):
    _assert_contains_all(install_docker_text, markers, message)


def test_install_docker_doc_lists_expected_outputs_for_each_step(
    install_docker_text: str,
):
    """Every numbered validation step must have an expected
    output / failure paste guidance attached. The shape (a
    numbered list 1..8 with concrete shapes) is the contract;
    we assert each numbered marker is present alongside an
    expected-output cue."""
    # The numbered markdown list `1. **`docker compose ...`**`
    # uses bold backtick'd commands.
    expected_markers = (
        "1. **`docker compose config`**",
        "2. **`docker compose build backend frontend`**",
        "3. **`docker compose up -d --wait backend`**",
        "4. **`curl -fsS http://127.0.0.1:5004/health`**",
        "5. **`docker compose up -d worker beat`**",
        "6. **`docker compose up -d --wait frontend`**",
        "7. **`curl -fsS http://127.0.0.1:3000/`**",
        "8. **`cleanroom.ping`**",
        "9. **memory caps visibility**",
        "10. **`docker compose stop`**",
    )
    _assert_contains_all(
        install_docker_text,
        expected_markers,
        "install-docker.md validation pack must include numbered "
        "expected-output entry {marker!r}. Dropping one creates an "
        "unverifiable step.",
    )


def test_install_docker_doc_documents_paste_back_artifacts(
    install_docker_text: str,
):
    # Marco's report-back must include host metadata so the
    # validation report has a complete fingerprint.
    for marker in (
        "uname -a",
        "docker --version",
        "docker compose version",
        "/etc/os-release",
    ):
        assert marker in install_docker_text, (
            f"install-docker.md validation pack must request "
            f"host metadata {marker!r} as part of the paste-"
            "back artifacts. Without the host fingerprint the "
            "validation report is not reproducible."
        )


def test_install_docker_doc_documents_script_non_destructive_contract(
    install_docker_text: str,
):
    # The doc must call out the script's non-destructive
    # contract so operators know what the wrapper avoids.
    assert "non-destructive" in install_docker_text.lower(), (
        "install-docker.md validation pack section must "
        "describe the script's non-destructive contract."
    )
    assert "docker compose down" in install_docker_text, (
        "install-docker.md must explicitly call out that "
        "`docker compose down` (and its `-v` variant) are NOT "
        "run by the validation pack."
    )


# BE-GO-READY-INSTALL-019: FE gap warnings in script output


def test_validate_script_defines_docker_path_report_function(
    validate_script_text: str,
):
    """BE-GO-READY-INSTALL-019: `report_docker_path_status` must be defined
    in the script to make Docker path truth visible to the operator at
    pack-start instead of requiring them to read the docs separately.

    The artifacts it reports match the source locks in
    `test_install_docker_fe_gap_guard.py`."""
    text = validate_script_text
    assert "report_docker_path_status() {" in text, (
        "scripts/docker-validate.sh must define `report_docker_path_status` "
        "(BE-GO-READY-INSTALL-019). The function reports Docker "
        "artifacts missing from the current scaffold so the "
        "operator sees the gaps upfront without reading the docs."
    )


def test_validate_script_fe_gap_report_has_canonical_warn_messages(
    validate_script_text: str,
):
    """The three canonical WARN messages must exist in the script.

    Each string corresponds to one of the three negative locks in
    `test_install_docker_fe_gap_guard.py`. Dropping one silently hides
    that gap class from the operator-facing report."""
    text = validate_script_text
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


def test_validate_script_ci_gap_check_looks_for_run_steps_not_comments(
    validate_script_text: str,
):
    """The CI detector must not treat a workflow comment that mentions
    `docker compose` as a wired Docker CI lane. It should look for a
    workflow `run:` step that invokes docker compose."""
    text = validate_script_text
    assert "^[[:space:]]*-[[:space:]]*run:" in text, (
        "scripts/docker-validate.sh must detect Docker CI from "
        "workflow `run:` steps, not arbitrary comments/doc strings."
    )
    assert "grep -rlF \"docker compose\" .github/workflows/" not in text, (
        "A plain recursive grep would count comments mentioning "
        "`docker compose` as a real CI lane. Keep the run-step "
        "regex instead."
    )


def test_validate_script_calls_docker_path_report_before_run_step_1(
    validate_script_text: str,
):
    """report_docker_path_status must be CALLED before run_step 1 so the
    operator sees the Docker path summary before the pack starts executing.

    A call that appears only after step 1 (or not at all) means the
    gaps are invisible until after the potentially long build begins."""
    text = validate_script_text
    call_pos = text.find("\nreport_docker_path_status\n")
    step1_pos = text.find("run_step 1 ")
    assert call_pos != -1, (
        "scripts/docker-validate.sh must invoke `report_docker_path_status` "
        "as a standalone call (not just define it) "
        "(BE-GO-READY-INSTALL-019)."
    )
    assert step1_pos != -1, (
        "run_step 1 must exist in scripts/docker-validate.sh "
        "(locked by test_validate_script_runs_eight_canonical_commands)."
    )
    assert call_pos < step1_pos, (
        "report_docker_path_status must be called BEFORE `run_step 1` "
        "(BE-GO-READY-INSTALL-019). A late call means the Docker path "
        "summary appears only after the build has started."
    )
