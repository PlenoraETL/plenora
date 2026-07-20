"""
Architectural lock for `scripts/install.sh` cleanroom packaging guard.

BE-GO-READY-INSTALLATION-013-CLEANROOM-PACKAGING-GUARD.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_INSTALL_SH = _REPO_ROOT / "scripts" / "install.sh"


@pytest.fixture(scope="module")
def install_sh_text() -> str:
    return _INSTALL_SH.read_text(encoding="utf-8")


def _bash_function(text: str, name: str) -> str:
    start = text.index(f"\n{name}() {{")
    end = text.index("\n}\n", start)
    return text[start:end]


def _array_block(text: str, name: str) -> str:
    start = text.index(f"{name}=(")
    end = text.index(")", start)
    return text[start:end]


def _assert_contains_all(text: str, markers: tuple[str, ...], message: str) -> None:
    for marker in markers:
        assert marker in text, message.format(marker=marker)


# 1. Required commands array stays canonical


_EXPECTED_REQUIRED_COMMANDS = (
    "python3.12",
    "node",
    "npm",
    "psql",
    "curl",
    "git",
)


def test_installer_required_commands_array_is_canonical(install_sh_text: str):
    assert "INSTALLER_REQUIRED_COMMANDS=(" in install_sh_text, (
        "Single source of truth for required commands missing - "
        "INSTALLER_REQUIRED_COMMANDS array must exist."
    )
    _assert_contains_all(
        _array_block(install_sh_text, "INSTALLER_REQUIRED_COMMANDS"),
        _EXPECTED_REQUIRED_COMMANDS,
        "INSTALLER_REQUIRED_COMMANDS missing canonical entry: {marker!r}. "
        "Removing or renaming an entry breaks the cleanroom acceptance "
        "contract documented in docs/ops/install-non-docker.md section 12.1.",
    )


# 2. Python pin matches CI


def test_installer_pins_python_major_minor_to_3_12(install_sh_text: str):
    assert (
        'INSTALLER_REQUIRED_PYTHON_MAJOR_MINOR="3.12"' in install_sh_text
    ), (
        "Python major.minor pin must remain 3.12 to match the CI lanes "
        "(.github/workflows/ci-cleanroom.yml etc.). Bumping requires a "
        "coordinated update of CI workflows and this lock."
    )


# 3. Native shared lib probe mentions gdal


def test_installer_shared_lib_probe_includes_gdal(install_sh_text: str):
    assert "INSTALLER_REQUIRED_SHARED_LIBS=(" in install_sh_text, (
        "INSTALLER_REQUIRED_SHARED_LIBS array missing - the cleanroom "
        "guard MUST probe at least one native shared lib."
    )
    assert "gdal:" in _array_block(install_sh_text, "INSTALLER_REQUIRED_SHARED_LIBS"), (
        "Native lib probe must include 'gdal' - it is the only "
        "non-pip-bundled runtime dep (pyogrio/geopandas import it via "
        "ctypes at runtime). Without this probe a host can pass `pip "
        "install -r requirements.txt` and still fail at first GeoPackage "
        "read."
    )


# 4. CLI flags wired and short-circuit


@pytest.mark.parametrize(
    ("flag", "assignment", "default"),
    (
        ("--check)", "CHECK_ONLY=1", None),
        ("--smoke)", "SMOKE_ONLY=1", "SMOKE_ONLY=0"),
    ),
)
def test_installer_mode_flag_is_parsed(
    install_sh_text: str,
    flag: str,
    assignment: str,
    default: str | None,
):
    assert flag in install_sh_text, f"`{flag[:-1]}` flag must be parsed."
    assert assignment in install_sh_text, (
        f"`{flag[:-1]}` must set {assignment!r} in the argv loop."
    )
    if default is not None:
        assert default in install_sh_text, (
            f"Default value {default!r} must be initialized before the "
            "mode branch is evaluated under `set -u`."
        )


@pytest.mark.parametrize(
    ("mode_expr", "summary_step", "must_precede"),
    (
        ("(( CHECK_ONLY ))", "step_check_only_summary", ("step_gather_config",)),
        (
            "(( SMOKE_ONLY ))",
            "step_smoke_summary",
            ("step_gather_config", "step_probe_services", "step_write_env"),
        ),
    ),
)
def test_installer_mode_branch_short_circuits_before_prompting_or_writes(
    install_sh_text: str,
    mode_expr: str,
    summary_step: str,
    must_precede: tuple[str, ...],
):
    main_body = _bash_function(install_sh_text, "main")
    assert mode_expr in main_body, (
        f"main() must branch on {mode_expr} before the full install pipeline."
    )
    assert summary_step in main_body, (
        f"main() must invoke {summary_step} in the mode branch so it exits "
        "with a structured PASS/FAIL summary."
    )
    mode_pos = main_body.index(mode_expr)
    for later_step in must_precede:
        assert mode_pos < main_body.index(later_step), (
            f"{mode_expr} branch must short-circuit BEFORE {later_step}; "
            "otherwise the read-only mode would prompt, connect to services, "
            "or mutate backend/.env."
        )


# 5. step_validate_packaging exists and is invoked in main


def test_step_validate_packaging_defined_and_invoked(install_sh_text: str):
    assert "step_validate_packaging() {" in install_sh_text, (
        "step_validate_packaging function must exist - it owns the "
        "native packaging probes (shared libs, pg_dump version, venv "
        "smoke import)."
    )
    main_body = _bash_function(install_sh_text, "main")
    # Must be invoked at least twice: once on the --check branch, once
    # on the full-install branch.
    assert main_body.count("step_validate_packaging") >= 2, (
        "step_validate_packaging must be invoked in BOTH the --check "
        "branch and the full-install branch."
    )


# 6. ASCII-only


def test_install_sh_is_ascii_only(install_sh_text: str):
    non_ascii = [
        (i, ch)
        for i, ch in enumerate(install_sh_text)
        if ord(ch) > 127
    ]
    assert not non_ascii, (
        f"scripts/install.sh must be ASCII-only (per BE-GO-READY-"
        f"INSTALLATION-012 scope rule). Found {len(non_ascii)} "
        f"non-ASCII char(s); first at offset {non_ascii[0][0]}: "
        f"U+{ord(non_ascii[0][1]):04X}."
    )


# 7. No destructive package install automation


_DESTRUCTIVE_PATTERNS = (
    "apt install",
    "apt-get install",
    "dnf install",
    "yum install",
    "pacman -S",
    "brew install",
    "snap install",
)


def test_install_sh_does_not_invoke_package_managers(install_sh_text: str):
    # Comments, log strings, and quoted hint text are allowed to MENTION
    # the apt one-liner ("apt install redis-server" as a hint to the
    # operator). The ban is on actual invocation: a line that *starts*
    # (after leading whitespace and an optional `sudo`) with one of the
    # patterns. This is the heuristic that matches "the script runs
    # apt install" without false-positives on hint strings.
    offending = []
    for lineno, line in enumerate(install_sh_text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Strip an optional leading `sudo ` so we catch `sudo apt
        # install ...` too.
        head = stripped
        if head.startswith("sudo "):
            head = head[len("sudo "):].lstrip()
        for pat in _DESTRUCTIVE_PATTERNS:
            if head.startswith(pat):
                offending.append((lineno, pat, line.rstrip()))
                break
    assert not offending, (
        "scripts/install.sh must NOT invoke a package manager "
        "(no destructive install automation per task brief). "
        f"Offending lines: {offending}"
    )


# 8. Sanity: file exists and is non-trivial


def test_install_sh_exists_and_is_executable_text():
    assert _INSTALL_SH.is_file(), (
        f"Expected installer at {_INSTALL_SH}; not found."
    )
    # The script must start with a bash shebang.
    head = _INSTALL_SH.read_text(encoding="utf-8").splitlines()[0]
    assert head.startswith("#!"), (
        f"scripts/install.sh first line must be a shebang; found: {head!r}"
    )
    assert "bash" in head, (
        f"scripts/install.sh shebang must invoke bash; found: {head!r}"
    )


# 9. --smoke flag wired and short-circuits without prompts/writes
#
# BE-GO-READY-INSTALL-009-SMOKE-SCRIPT-GUARDS. The --smoke option is
# the post-install verification hook (rows 7-8 of the canonical
# clean-install harness). The contract: it MUST be parsed in argv,
# documented in the usage block, branched on inside main() BEFORE any
# prompt/write step, and it MUST NOT mutate state.


def test_smoke_flag_documented_in_usage_block(install_sh_text: str):
    # `usage()` renders the comment block in lines 2..50 of the
    # script (it `sed -n '2,50p' "$0" | sed 's/^# \{0,1\}//'`).
    # The Usage section must therefore mention --smoke inside that
    # range so `bash scripts/install.sh --help` surfaces the option.
    header_lines = install_sh_text.splitlines()[1:50]
    head = "\n".join(header_lines)
    assert "--smoke" in head, (
        "Usage header (lines 2-50, rendered by `usage()`) must "
        "document the --smoke flag so `bash scripts/install.sh "
        "--help` shows it."
    )
    # Pin the canonical one-line description so a future edit cannot
    # silently demote --smoke to a no-op or rename it.
    assert "post-install smoke" in head, (
        "Usage header must describe --smoke as 'post-install smoke' "
        "to align with the canonical wording in "
        "docs/ops/install-marco-dry-run.md section 5.0."
    )


# 10. INSTALLER_SMOKE_MEMORY_CAPS canonical (six caps)
#
# The smoke harness reads runtime-effective memory caps via
# `app.config.settings.get(...)`. The list is the single source of
# truth on the script side; the doc cross-checks the same names via
# `test_install_smoke_harness_doc_guard.py`. ARTIFACT_PREVIEW_MAX_BYTES
# joined the canonical set in INSTALL-009.


_EXPECTED_SMOKE_MEMORY_CAPS = (
    "LOAD_FILE_MAX_BYTES",
    "WORKER_RSS_MAX_BYTES",
    "RESOURCE_MAX_BYTES",
    "ARTIFACT_PREVIEW_MAX_BYTES",
    "CROSS_JOIN_MAX_OUTPUT_ROWS",
    "MULTI_JOIN_MAX_OUTPUT_ROWS",
)


def test_installer_smoke_memory_caps_array_is_canonical(
    install_sh_text: str,
):
    assert "INSTALLER_SMOKE_MEMORY_CAPS=(" in install_sh_text, (
        "INSTALLER_SMOKE_MEMORY_CAPS array must exist as the script-"
        "side single source of truth for the smoke caps loop."
    )
    _assert_contains_all(
        _array_block(install_sh_text, "INSTALLER_SMOKE_MEMORY_CAPS"),
        _EXPECTED_SMOKE_MEMORY_CAPS,
        "INSTALLER_SMOKE_MEMORY_CAPS missing canonical cap: {marker!r}. "
        "Dropping a cap here means the runtime guard becomes invisible "
        "at install time. ARTIFACT_PREVIEW_MAX_BYTES is included in this "
        "canonical set for the intermediate artifact preview cap.",
    )


# 11. Smoke branch is non-destructive (no writes, no service mutation)


def _slice_smoke_block(text: str) -> str:
    """Return the source region containing every step_smoke_* function
    plus the SMOKE_ONLY branch in main(), so the heuristics below
    inspect only the smoke implementation, not the surrounding
    installer body."""
    start_marker = "step_smoke_caps_visibility() {"
    if start_marker not in text:
        return ""
    start = text.index(start_marker)
    # Stop at step_check_only_summary which sits adjacent to the
    # smoke functions (the check-summary helper post-dates the smoke
    # block and is not part of --smoke).
    end_marker = "step_check_only_summary() {"
    end = text.find(end_marker, start)
    if end == -1:
        end = len(text)
    return text[start:end]


_SMOKE_FORBIDDEN_PATTERNS = (
    # Filesystem mutation primitives: --smoke must not write or
    # delete anything in the repo or in /etc.
    "mv ",
    "rm ",
    "rmdir ",
    "truncate ",
    "tee ",
    "chmod ",
    "chown ",
    # systemd / service mutation.
    "systemctl start ",
    "systemctl stop ",
    "systemctl restart ",
    "systemctl enable ",
    "systemctl disable ",
    # Package managers (already enforced globally; restated here for
    # locality so a smoke-section regression surfaces in this test).
    "apt install",
    "apt-get install",
)


def test_smoke_branch_does_not_mutate_state(install_sh_text: str):
    smoke_block = _slice_smoke_block(install_sh_text)
    assert smoke_block, (
        "Could not locate the smoke section (step_smoke_* functions). "
        "Either the section was removed or its anchor renamed; both "
        "are regressions of the --smoke contract."
    )
    offending: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(smoke_block.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        for pat in _SMOKE_FORBIDDEN_PATTERNS:
            if pat in stripped:
                offending.append((lineno, pat, line.rstrip()))
                break
    assert not offending, (
        "Smoke section must be non-destructive (no writes, no "
        "service mutation, no package install). Offending lines "
        "(numbered relative to the smoke block):\n"
        + "\n".join(f"  {ln}: {pat!r} -> {src}" for ln, pat, src in offending)
    )


def test_smoke_branch_does_not_call_write_env_or_probe_services(
    install_sh_text: str,
):
    smoke_block = _slice_smoke_block(install_sh_text)
    # step_write_env writes backend/.env; step_probe_services makes
    # network calls; step_gather_config prompts. None must appear in
    # the smoke implementation.
    for forbidden in (
        "step_write_env",
        "step_probe_services",
        "step_gather_config",
    ):
        assert forbidden not in smoke_block, (
            f"--smoke implementation must not call {forbidden}. "
            "Smoke is read-only by contract; calling that helper "
            "would either prompt, mutate the .env, or initiate "
            "service probes outside the operator-controlled smoke "
            "knobs (PLENORA_SMOKE_*)."
        )


# 12. Optional health/ready curl uses bounded --max-time


def test_smoke_health_ready_curl_uses_max_time(install_sh_text: str):
    smoke_block = _slice_smoke_block(install_sh_text)
    # Every curl INVOCATION in the smoke section must carry an
    # explicit `--max-time N` so a hung uvicorn cannot block the
    # smoke harness indefinitely. We match lines whose first token
    # (after optional `if`/`!`/whitespace) is `curl` - this skips
    # `command -v curl` checks while catching `curl ...` and
    # `if curl ...` invocations.
    curl_lines: list[str] = []
    for raw in smoke_block.splitlines():
        line = raw.lstrip()
        if line.startswith("#"):
            continue
        # Strip a leading `if ` or `if ! ` so the heuristic catches
        # `if curl ...` invocation blocks.
        if line.startswith("if "):
            line = line[len("if "):].lstrip()
        if line.startswith("! "):
            line = line[len("! "):].lstrip()
        if line.startswith("curl "):
            curl_lines.append(raw)
    assert curl_lines, (
        "Smoke section must invoke curl at least once (health/ready "
        "probe). None found - the smoke 3 step regressed."
    )
    for line in curl_lines:
        assert "--max-time" in line, (
            "Every curl call in the smoke section must include "
            f"`--max-time` to bound the request. Offending line: {line!r}"
        )


# 13. cleanroom.ping dispatch + bounded PostgreSQL poll (Dramatiq)


def test_smoke_cleanroom_ping_uses_bounded_poll(install_sh_text: str):
    smoke_block = _slice_smoke_block(install_sh_text)
    assert "cleanroom.ping" in smoke_block, (
        "Smoke section must invoke `cleanroom.ping` (the canonical "
        "deterministic primitive shared with the cleanroom CI lane)."
    )
    # Plenora 2.0 Fase 5: il broker e' RabbitMQ (Dramatiq) e l'esito e' su
    # PostgreSQL (task_result_store), non piu' AsyncResult/Redis. Il dispatch
    # va per nome e lo stato si polla con una DEADLINE limitata (mai un loop
    # illimitato).
    assert "dispatch_task_by_name" in smoke_block, (
        "cleanroom.ping smoke must dispatch by-name via "
        "`dispatch_task_by_name` (Dramatiq), non piu' celery_app.send_task."
    )
    assert "time.monotonic() + 30" in smoke_block, (
        "cleanroom.ping smoke must poll the result-store with a BOUNDED "
        "deadline (`time.monotonic() + 30`), non un loop illimitato. "
        "Locked by section 5.3 of docs/ops/install-marco-dry-run.md."
    )


# 14. Smoke branch reads only the operator-controlled smoke env vars


def test_smoke_branch_only_reads_smoke_env_vars(install_sh_text: str):
    smoke_block = _slice_smoke_block(install_sh_text)
    forbidden_install_vars = (
        "PLENORA_INSTALL_DATABASE_URL",
        "PLENORA_INSTALL_BROKER_URL",
        "PLENORA_INSTALL_STORAGE_POLICY",
    )
    for var_name in forbidden_install_vars:
        assert var_name not in smoke_block, (
            f"--smoke must not read {var_name}. The smoke flag is "
            "post-install verification; it consumes only PLENORA_SMOKE_* "
            "env vars."
        )
    _assert_contains_all(
        smoke_block,
        ("PLENORA_SMOKE_BACKEND_URL", "PLENORA_SMOKE_RUN_PING"),
        "--smoke must consult {marker} (documented in the usage header "
        "and in docs/ops/install-marco-dry-run.md section 5.0).",
    )


# 15. --check reports env file + venv status (BE-GO-READY-INSTALL-020)


def test_check_env_status_reports_env_file_and_venv(install_sh_text: str):
    """The function body must reference ENV_FILE (for .env presence
    check) and 'venv' (for virtual-environment check), and emit
    'EXISTS' as the positive signal when backend/.env is found."""
    fn_start = install_sh_text.index("step_check_env_status() {")
    # Slice up to the next comment section marker so we don't bleed
    # into adjacent functions.
    next_section = install_sh_text.index(
        "# Step 2 - Gather configuration", fn_start
    )
    fn_text = install_sh_text[fn_start:next_section]
    _assert_contains_all(
        fn_text,
        ("ENV_FILE", "venv", "EXISTS"),
        "step_check_env_status must include {marker!r} so --check reports "
        "backend/.env and backend/venv existence without reading secrets "
        "or writing anything (BE-GO-READY-INSTALL-020).",
    )


def test_check_env_status_called_in_check_only_branch(install_sh_text: str):
    """step_check_env_status must be called inside the CHECK_ONLY
    branch of main(), before step_gather_config (which prompts) and
    before step_check_only_summary (so the env/venv status appears
    before the final PASS/FAIL line)."""
    main_body = _bash_function(install_sh_text, "main")
    assert "step_check_env_status" in main_body, (
        "step_check_env_status must be called inside main() "
        "(BE-GO-READY-INSTALL-020). Currently absent from main."
    )
    env_pos = main_body.index("step_check_env_status")
    gather_pos = main_body.index("step_gather_config")
    summary_pos = main_body.index("step_check_only_summary")
    assert env_pos < gather_pos, (
        "step_check_env_status must be called BEFORE "
        "step_gather_config so --check stays prompt-free "
        "(BE-GO-READY-INSTALL-020)."
    )
    assert env_pos < summary_pos, (
        "step_check_env_status must be called BEFORE "
        "step_check_only_summary so the env/venv status appears "
        "in the --check transcript before the final PASS/FAIL "
        "verdict (BE-GO-READY-INSTALL-020)."
    )


# 16. INSTALLER_MANAGED_KEYS all documented in backend/.env.example
#
# BE-GO-READY-ARCH-059-W6: Every key in INSTALLER_MANAGED_KEYS must
# appear in backend/.env.example so operators know what the installer
# writes on every run. This guard prevents silent drift where a new
# installer-managed key is added to install.sh without a corresponding
# operator-facing entry in the template.


_ENV_EXAMPLE = _REPO_ROOT / "backend" / ".env.example"


def _parse_installer_managed_keys(text: str) -> set:
    start = text.index("INSTALLER_MANAGED_KEYS=(")
    end = text.index(")", start)
    array_block = text[start + len("INSTALLER_MANAGED_KEYS=("):end]
    keys = set()
    for line in array_block.splitlines():
        token = line.strip()
        if token and not token.startswith("#"):
            keys.add(token)
    return keys


def _parse_env_example_keys(text: str) -> set:
    import re
    # Match `KEY=` and `# KEY=` lines where KEY starts the non-comment
    # portion - avoids matching KEY= inside prose or code examples such
    # as `python -c "... KEY=..."` (those lines start with non-uppercase
    # tokens before the key name).
    pattern = re.compile(r"^#?\s*([A-Z_][A-Z0-9_]*)=", re.MULTILINE)
    return {m.group(1) for m in pattern.finditer(text)}


@pytest.fixture(scope="module")
def env_example_text() -> str:
    assert _ENV_EXAMPLE.is_file(), f"backend/.env.example not found at {_ENV_EXAMPLE}"
    return _ENV_EXAMPLE.read_text(encoding="utf-8")


def test_installer_managed_keys_all_documented_in_env_example(
    install_sh_text: str,
    env_example_text: str,
):
    """Every key in INSTALLER_MANAGED_KEYS must appear in
    backend/.env.example so operators know what the installer writes.

    Failure means a key was added to INSTALLER_MANAGED_KEYS without a
    corresponding commented entry in the operator-facing template.
    Add `# KEY=<default>` and a short description to .env.example.
    """
    managed = _parse_installer_managed_keys(install_sh_text)
    documented = _parse_env_example_keys(env_example_text)
    missing = managed - documented
    assert not missing, (
        f"INSTALLER_MANAGED_KEYS key(s) not documented in "
        f"backend/.env.example: {sorted(missing)}. "
        f"Add a commented `# KEY=` entry with a short description "
        f"so operators know the installer writes these keys. "
        f"(BE-GO-READY-ARCH-059-W6-CLEAN-INSTALL-AUTOMATION-CUT)"
    )
