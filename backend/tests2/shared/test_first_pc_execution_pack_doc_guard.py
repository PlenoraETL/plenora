"""
Source-bound lock for the first-PC execution pack.

BE-GO-READY-INSTALL-015-FIRST-PC-EXECUTION-PACK.
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
_REPORT_TPL = (
    _REPO_ROOT / "docs" / "ops" / "first-pc-validation-report-template.md"
)


_ENTRY_HEADER = "## 0. First-PC handoff pack (entry point)"
_ENTRY_END_MARKER = "\n## 1. Manual choices"


@pytest.fixture(scope="module")
def dry_run_text() -> str:
    return _DRY_RUN.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def entry_section(dry_run_text: str) -> str:
    if _ENTRY_HEADER not in dry_run_text:
        return ""
    start = dry_run_text.index(_ENTRY_HEADER)
    end = dry_run_text.find(_ENTRY_END_MARKER, start)
    if end == -1:
        end = len(dry_run_text)
    return dry_run_text[start:end]


@pytest.fixture(scope="module")
def report_text() -> str:
    return _REPORT_TPL.read_text(encoding="utf-8")


# Dry-run pack section 0


def test_entry_section_exists(entry_section: str):
    assert entry_section, (
        "docs/ops/install-marco-dry-run.md must contain section 0 "
        f"with header {_ENTRY_HEADER!r}. Without the entry-point "
        "section, the first-PC handoff has no canonical entry; "
        "operators arriving on the dry-run page see only the "
        "command sequence and miss the report template."
    )
    assert "BE-GO-READY-INSTALL-015-FIRST-PC-EXECUTION-PACK" in entry_section, (
        "Section 0 must keep the BE-GO-READY-INSTALL-015 stream "
        "tag for audit traceability."
    )


def test_entry_section_names_five_steps(entry_section: str):
    # The five-step canonical handoff. Each step's anchor phrase
    # must be present so the cross-document mapping is intact.
    expected_anchors = (
        ("Clean clone + pinned commit hash",
         "Section 0 must include step 1 'Clean clone + pinned "
         "commit hash' so the operator knows to record the "
         "exact commit being tested."),
        ("Non-Docker path (go-ready)",
         "Section 0 must include step 2 'Non-Docker path "
         "(go-ready)' so the canonical first-PC test path is "
         "named explicitly."),
        ("Docker validation path",
         "Section 0 must include step 3 'Docker validation "
         "path' so the Docker path is named explicitly."),
        ("Report template",
         "Section 0 must include step 4 'Report template' so "
         "the operator knows where to paste back."),
        ("Stop on failure",
         "Section 0 must include step 5 'Stop on failure' so "
         "the operator knows when to halt."),
    )
    for needle, message in expected_anchors:
        assert needle in entry_section, message


def test_entry_section_keeps_docker_first_scaffold_label(entry_section: str):
    # The Docker path must remain labelled FIRST SCAFFOLD / NOT
    # YET VALIDATED inside the entry-point section. A future edit
    # that softens this to "Docker path (alternative)" would let
    # an operator mistake the scaffold for a validated path.
    assert "FIRST SCAFFOLD" in entry_section, (
        "Section 0 must keep the 'FIRST SCAFFOLD' label on the "
        "Docker path. INSTALL-015 explicitly does NOT promote "
        "Docker to validated."
    )
    assert "NOT YET VALIDATED" in entry_section, (
        "Section 0 must keep the 'NOT YET VALIDATED' qualifier "
        "on the Docker path. Without this, an operator may "
        "treat the scaffold's syntactically-correct commands as "
        "a validated runbook."
    )


def test_entry_section_links_report_template(entry_section: str):
    assert "first-pc-validation-report-template.md" in entry_section, (
        "Section 0 must cross-link to "
        "`first-pc-validation-report-template.md` so the report "
        "template is one click from the entry point. Without the "
        "link, the template orphans."
    )


def test_entry_section_is_ascii_only(entry_section: str):
    non_ascii = [
        (i, ch) for i, ch in enumerate(entry_section)
        if ord(ch) > 127
    ]
    assert not non_ascii, (
        f"Section 0 must be ASCII-only. Found "
        f"{len(non_ascii)} non-ASCII char(s); first at offset "
        f"{non_ascii[0][0]}: U+{ord(non_ascii[0][1]):04X}."
    )


# Report template


def test_report_template_exists():
    assert _REPORT_TPL.is_file(), (
        "docs/ops/first-pc-validation-report-template.md must "
        "exist. Removing it orphans the dry-run pack section 0 "
        "cross-link and breaks the handoff loop."
    )


def test_report_template_has_canonical_title(report_text: str):
    assert report_text.startswith(
        "# First-PC validation report template\n"
    ), (
        "Report template must start with the canonical title "
        "'# First-PC validation report template'. Renaming "
        "silently breaks the cross-link in dry-run pack "
        "section 0."
    )


def test_report_template_has_canonical_sections(report_text: str):
    expected_sections = (
        "## 0. Host snapshot",
        "## 1. Clone + commit hash",
        "## 2. Non-Docker path",
        "## 3. Docker validation path",
        "## 4. Where I stopped",
        "## 5. Time spent",
        "## 6. Free-form notes",
        "## Stop conditions",
        "## What \"validated install\" means",
    )
    for section in expected_sections:
        assert section in report_text, (
            f"Report template must contain section header "
            f"{section!r}. The 0-6 + stop conditions + "
            "validation-criterion sections form the contract "
            "surface for what Marco pastes back."
        )


def test_report_template_keeps_docker_first_scaffold_label(report_text: str):
    assert "FIRST SCAFFOLD" in report_text, (
        "Report template must keep the 'FIRST SCAFFOLD' label "
        "on the Docker path. Without it the template reads as "
        "if both paths are equally validated."
    )
    assert "NOT YET VALIDATED" in report_text, (
        "Report template must keep the 'NOT YET VALIDATED' "
        "qualifier on the Docker path."
    )


def test_report_template_documents_stop_conditions(report_text: str):
    # The "Stop conditions" section enumerates the canonical
    # halt points. Each name must appear so the operator knows
    # which failure mode triggers a stop.
    stop_idx = report_text.find("## Stop conditions")
    assert stop_idx != -1
    stop_section = report_text[stop_idx:]
    expected_phrases = (
        "--check",
        "/ready",
        "503",
        "docker compose build",
        "cleanroom.ping",
    )
    for needle in expected_phrases:
        assert needle in stop_section, (
            f"Stop-conditions section must reference {needle!r} "
            "as a canonical halt trigger. Without it the operator "
            "may push past a real failure."
        )


def test_report_template_links_back_to_dry_run_pack(report_text: str):
    # Bidirectional links keep the handoff loop discoverable.
    expected_links = (
        "install-marco-dry-run.md",
        "install-non-docker.md",
        "install-docker.md",
    )
    for link in expected_links:
        assert link in report_text, (
            f"Report template must link to {link!r} so the "
            "operator can navigate back to the procedural docs "
            "from the report template."
        )


def test_report_template_is_ascii_only(report_text: str):
    non_ascii = [
        (i, ch) for i, ch in enumerate(report_text)
        if ord(ch) > 127
    ]
    assert not non_ascii, (
        f"Report template must be ASCII-only. Found "
        f"{len(non_ascii)} non-ASCII char(s); first at offset "
        f"{non_ascii[0][0]}: U+{ord(non_ascii[0][1]):04X}."
    )
