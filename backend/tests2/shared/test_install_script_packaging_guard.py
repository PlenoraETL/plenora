"""Architectural lock for `scripts/install.sh` cleanroom packaging guard.

BE-GO-READY-INSTALLATION-013-CLEANROOM-PACKAGING-GUARD.

Source-bound assertions on the installer script. The guard exists to
catch silent regressions of the cleanroom acceptance contract
documented in `docs/ops/install-non-docker.md` section 12 (Cleanroom
acceptance targets):

  - the canonical list of required host commands stays at the
    documented 7 entries (python3.11, node, npm, psql, redis-cli,
    curl, git);
  - the required Python major.minor stays pinned at "3.11" so the
    installer agrees with all CI lanes
    (`.github/workflows/{ci,ci-cleanroom,ci-functional}.yml`);
  - the native shared library probe still mentions `gdal` (the only
    non-pip-bundled native dep at runtime);
  - the `--check` preflight flag is wired to `CHECK_ONLY` and the
    script branches to the validators-only path before any prompt
    or service probe;
  - the script is ASCII-only (per scope rule) and does NOT invoke
    `apt install` / `dnf install` / etc. (no destructive package
    install automation in the prototype).

These assertions are text-level on the script source. They do NOT
execute the script; they assert that the contract is preserved by
reading the file. If a future refactor silently drops a probe, this
test fails before any operator sees a regressed installer.
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


# =====================================================================
# 1. Required commands array stays canonical
# =====================================================================


_EXPECTED_REQUIRED_COMMANDS = (
    "python3.11",
    "node",
    "npm",
    "psql",
    "redis-cli",
    "curl",
    "git",
)


def test_installer_required_commands_array_is_canonical(install_sh_text: str):
    assert "INSTALLER_REQUIRED_COMMANDS=(" in install_sh_text, (
        "Single source of truth for required commands missing - "
        "INSTALLER_REQUIRED_COMMANDS array must exist."
    )
    # Slice from the array opener to its closing paren (single-quoted
    # search; bash arrays are line-delimited in this script).
    start = install_sh_text.index("INSTALLER_REQUIRED_COMMANDS=(")
    end = install_sh_text.index(")", start)
    array_block = install_sh_text[start:end]
    for cmd in _EXPECTED_REQUIRED_COMMANDS:
        assert cmd in array_block, (
            f"INSTALLER_REQUIRED_COMMANDS missing canonical entry: {cmd!r}. "
            "Removing or renaming an entry breaks the cleanroom acceptance "
            "contract documented in docs/ops/install-non-docker.md section 12.1."
        )


# =====================================================================
# 2. Python pin matches CI
# =====================================================================


def test_installer_pins_python_major_minor_to_3_11(install_sh_text: str):
    assert (
        'INSTALLER_REQUIRED_PYTHON_MAJOR_MINOR="3.11"' in install_sh_text
    ), (
        "Python major.minor pin must remain 3.11 to match the CI lanes "
        "(.github/workflows/ci-cleanroom.yml etc.). Bumping requires a "
        "coordinated update of CI workflows and this lock."
    )


# =====================================================================
# 3. Native shared lib probe mentions gdal
# =====================================================================


def test_installer_shared_lib_probe_includes_gdal(install_sh_text: str):
    assert "INSTALLER_REQUIRED_SHARED_LIBS=(" in install_sh_text, (
        "INSTALLER_REQUIRED_SHARED_LIBS array missing - the cleanroom "
        "guard MUST probe at least one native shared lib."
    )
    start = install_sh_text.index("INSTALLER_REQUIRED_SHARED_LIBS=(")
    end = install_sh_text.index(")", start)
    array_block = install_sh_text[start:end]
    assert "gdal:" in array_block, (
        "Native lib probe must include 'gdal' - it is the only "
        "non-pip-bundled runtime dep (fiona/geopandas import it via "
        "ctypes at runtime). Without this probe a host can pass `pip "
        "install -r requirements.txt` and still fail at first GeoPackage "
        "read."
    )


# =====================================================================
# 4. --check flag wired and short-circuits
# =====================================================================


def test_check_flag_is_parsed(install_sh_text: str):
    assert "--check)" in install_sh_text, (
        "`--check` flag must be parsed in the argv loop."
    )
    assert "CHECK_ONLY=1" in install_sh_text, (
        "`--check` must set CHECK_ONLY=1."
    )


def test_check_only_short_circuits_before_prompts(install_sh_text: str):
    # main() must run validate_prereqs + validate_packaging then exit
    # via step_check_only_summary BEFORE step_gather_config (which
    # prompts the operator).
    main_start = install_sh_text.index("\nmain() {")
    main_end = install_sh_text.index("\n}\n", main_start)
    main_body = install_sh_text[main_start:main_end]

    assert "(( CHECK_ONLY ))" in main_body, (
        "main() must branch on CHECK_ONLY before running the full "
        "install pipeline."
    )
    assert "step_check_only_summary" in main_body, (
        "main() must invoke step_check_only_summary in the --check "
        "branch so the script exits with a structured PASS/FAIL summary."
    )
    # Ordering: the CHECK_ONLY branch must appear lexically BEFORE
    # step_gather_config (which prompts).
    check_pos = main_body.index("CHECK_ONLY")
    gather_pos = main_body.index("step_gather_config")
    assert check_pos < gather_pos, (
        "CHECK_ONLY branch must short-circuit BEFORE step_gather_config; "
        "otherwise --check would prompt the operator."
    )


# =====================================================================
# 5. step_validate_packaging exists and is invoked in main
# =====================================================================


def test_step_validate_packaging_defined_and_invoked(install_sh_text: str):
    assert "step_validate_packaging() {" in install_sh_text, (
        "step_validate_packaging function must exist - it owns the "
        "native packaging probes (shared libs, pg_dump version, venv "
        "smoke import)."
    )
    main_start = install_sh_text.index("\nmain() {")
    main_end = install_sh_text.index("\n}\n", main_start)
    main_body = install_sh_text[main_start:main_end]
    # Must be invoked at least twice: once on the --check branch, once
    # on the full-install branch.
    assert main_body.count("step_validate_packaging") >= 2, (
        "step_validate_packaging must be invoked in BOTH the --check "
        "branch and the full-install branch."
    )


# =====================================================================
# 6. ASCII-only
# =====================================================================


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


# =====================================================================
# 7. No destructive package install automation
# =====================================================================


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


# =====================================================================
# 8. Sanity: file exists and is non-trivial
# =====================================================================


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
