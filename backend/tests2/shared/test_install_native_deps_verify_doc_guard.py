"""
Architectural lock for the native-dependency verification doc.

BE-GO-READY-INSTALLATION-025-LINUX-NATIVE-DEPS-VERIFY.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DRY_RUN = _REPO_ROOT / "docs" / "ops" / "install-marco-dry-run.md"
_NON_DOCKER = _REPO_ROOT / "docs" / "ops" / "install-non-docker.md"


@pytest.fixture(scope="module")
def dry_run_text() -> str:
    return _DRY_RUN.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def non_docker_text() -> str:
    return _NON_DOCKER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def native_deps_section(dry_run_text: str) -> str:
    header = "## 3. Native dependency verification"
    if header not in dry_run_text:
        return ""
    start = dry_run_text.index(header)
    end_marker = "\n## 4. Failure triage checklist"
    end = dry_run_text.find(end_marker, start)
    if end == -1:
        end = len(dry_run_text)
    return dry_run_text[start:end]


# Section 3 of the dry-run pack


def test_native_deps_section_exists(native_deps_section: str):
    assert native_deps_section, (
        "docs/ops/install-marco-dry-run.md must contain section "
        "3 'Native dependency verification' between the command "
        "sequence and the failure triage. Removing it leaves "
        "Marco without explicit verification commands."
    )


def test_section_3_lists_os_binary_probes(native_deps_section: str):
    expected_probes = (
        ("python3.11 --version", "section 3.1 must include "
         "`python3.11 --version`."),
        ("pg_config --version", "section 3.1 must include "
         "`pg_config --version` (used by the backup unit + "
         "operator-side pg_hba visibility)."),
        ("psql --version", "section 3.1 must include "
         "`psql --version`."),
        ("redis-server --version", "section 3.1 must include "
         "`redis-server --version`."),
        ("redis-cli --version", "section 3.1 must include "
         "`redis-cli --version`."),
        ("gdalinfo --version", "section 3.1 MUST include "
         "`gdalinfo --version` - this is the canonical OS-level "
         "probe for the GDAL shared lib that fiona/geopandas "
         "load via ctypes at runtime."),
        ("node --version", "section 3.1 must include "
         "`node --version`."),
        ("npm --version", "section 3.1 must include `npm --version`."),
        ("curl --version", "section 3.1 must include "
         "`curl --version`."),
        ("git --version", "section 3.1 must include `git --version`."),
    )
    for needle, message in expected_probes:
        assert needle in native_deps_section, message


def test_section_3_includes_venv_python_import_probe(
    native_deps_section: str,
):
    # The venv probe is the surface that exercises every Python
    # wheel binding to a native lib. Modules below MUST be in the
    # probe.
    expected_modules = (
        "psycopg2",
        "redis",
        "celery",
        "fiona",
        "geopandas",
        "shapely",
        "pyarrow",
        "pandas",
        "filetype",
        "boto3",
    )
    for module in expected_modules:
        # Match by quoted string presence (the probe lists modules
        # in a Python tuple; quoting style is single-or-double).
        assert (
            f'"{module}"' in native_deps_section
            or f"'{module}'" in native_deps_section
        ), (
            f"section 3.2 venv import probe must list {module!r} as "
            "one of the modules whose import is asserted."
        )


def test_section_3_documents_fiona_transitive_via_geopandas(
    native_deps_section: str,
):
    # `fiona` is NOT a direct entry in requirements.txt today.
    # The doc must call this out so an operator hitting an ABI
    # mismatch knows the fix path.
    assert (
        "fiona" in native_deps_section
        and "transitive" in native_deps_section
        and "geopandas" in native_deps_section
    ), (
        "section 3.2 must document that `fiona` is transitive via "
        "`geopandas` (NOT a direct requirements.txt entry). This "
        "matters for ABI-mismatch troubleshooting."
    )


def test_section_3_excludes_libmagic_verification(
    native_deps_section: str,
):
    # filetype is pure-Python (requirements.txt:61). The dry-run
    # pack must NOT add a libmagic verification, because that
    # would imply the runtime needs libmagic - which is false.
    forbidden = (
        "import magic",
        "libmagic --version",
        "magic.from_file",
    )
    for needle in forbidden:
        assert needle not in native_deps_section, (
            f"section 3 must NOT verify {needle!r}. `filetype` is "
            "pure-Python (requirements.txt:61); adding a libmagic "
            "verification would imply a runtime dependency that "
            "does not exist."
        )
    # And the doc must explicitly say so so a future contributor
    # does not re-add the verification by mistake.
    assert (
        "libmagic" in native_deps_section.lower()
        and "pure-python" in native_deps_section.lower()
    ), (
        "section 3 must explicitly note that `libmagic` is NOT "
        "verified because `filetype` is pure-Python. Without the "
        "explicit note, a future contributor may add a libmagic "
        "verification and create a fake runtime dep."
    )


def test_section_3_includes_service_probes(native_deps_section: str):
    expected = (
        ("psql ", "section 3.3 must include the psql probe."),
        ("redis-cli", "section 3.3 must include the redis-cli probe."),
        ("SELECT current_user", "section 3.3 must include the "
         "Postgres `SELECT current_user, current_database(), "
         "version();` smoke."),
        ("has_database_privilege", "section 3.3 must include the "
         "`has_database_privilege` CREATE check - it surfaces the "
         "exact role-permission failure that breaks "
         "migrations_runner.ensure_schema."),
        ("PING", "section 3.3 must include `PING` (Redis "
         "reachability)."),
    )
    for needle, message in expected:
        assert needle in native_deps_section, message


def test_section_3_documents_distro_target(native_deps_section: str):
    assert (
        "Ubuntu 24.04" in native_deps_section
        and "Fedora" in native_deps_section
    ), (
        "section 3 must document the Ubuntu 24.04 reference target "
        "AND explicitly say Fedora / RHEL are NOT in scope (their "
        "package names differ entirely). Without the distro note, "
        "an operator on RHEL might run the apt one-liner and fail "
        "without diagnostic context."
    )


def test_section_3_is_ascii_only(native_deps_section: str):
    non_ascii = [
        (i, ch)
        for i, ch in enumerate(native_deps_section)
        if ord(ch) > 127
    ]
    assert not non_ascii, (
        f"section 3 of the dry-run pack must be ASCII-only. "
        f"Found {len(non_ascii)} non-ASCII char(s); first at offset "
        f"{non_ascii[0][0]}: U+{ord(non_ascii[0][1]):04X}."
    )


# install-non-docker.md mirror (section 1.2)


def test_install_non_docker_mirrors_binary_probes(
    non_docker_text: str,
):
    # Section 1.2 of the full runbook must mirror the OS-binary
    # probe list so an operator who never opens the dry-run pack
    # still has the verification commands.
    assert "### 1.2 Verify host packages" in non_docker_text, (
        "install-non-docker.md must contain section 1.2 'Verify "
        "host packages (post-apt)' so the runbook itself contains "
        "the probe list, not only the dry-run pack."
    )
    expected_probes = (
        "gdalinfo --version",
        "pg_config --version",
        "psql --version",
        "redis-server --version",
        "redis-cli --version",
        "node --version",
        "npm --version",
        "python3.11 --version",
    )
    for probe in expected_probes:
        assert probe in non_docker_text, (
            f"install-non-docker.md section 1.2 must include the "
            f"{probe!r} probe so the runbook agrees with the "
            "dry-run pack section 3.1."
        )
    assert "install-marco-dry-run.md" in non_docker_text, (
        "install-non-docker.md section 1.2 must cross-link to the "
        "dry-run pack so the operator can find the venv-import + "
        "service-probe layer."
    )


def test_install_non_docker_documents_libmagic_precaution(
    non_docker_text: str,
):
    # The runbook (not just the dry-run pack) must document that
    # libmagic1 is precautionary and not a real runtime dep.
    assert "libmagic1" in non_docker_text, (
        "install-non-docker.md must reference libmagic1 in the "
        "host-packages explanation."
    )
    # The note must explicitly say it's precautionary and NOT
    # required by any Plenora-ETL import.
    assert (
        "precautionary" in non_docker_text.lower()
        and "filetype" in non_docker_text
        and "pure-Python" in non_docker_text
    ), (
        "install-non-docker.md must contain the precautionary-not-"
        "required note for libmagic1 with filetype identified as "
        "the pure-Python alternative actually in use."
    )
