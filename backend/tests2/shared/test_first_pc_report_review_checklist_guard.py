"""
Source-bound guard for the first-PC report review / intake checklist.

BE-GO-READY-INSTALL-017-FIRST-PC-REPORT-INTAKE-CHECKLIST.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CHECKLIST = (
    _REPO_ROOT / "docs" / "ops" / "first-pc-report-review-checklist.md"
)
_TEMPLATE = (
    _REPO_ROOT / "docs" / "ops" / "first-pc-validation-report-template.md"
)


@pytest.fixture(scope="module")
def checklist_text() -> str:
    return _CHECKLIST.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def template_text() -> str:
    return _TEMPLATE.read_text(encoding="utf-8")


# 1. Top-level section headers present


_REQUIRED_HEADERS: tuple[tuple[str, str], ...] = (
    (
        "## 1. Intake -- did the report arrive in the expected shape?",
        "Section 1 (structural intake) must exist; without it a "
        "reviewer scores a partial paste as if it were a full "
        "report and produces a misleading verdict.",
    ),
    (
        "## 2. Non-Docker path scoring (go-ready candidate)",
        "Section 2 (non-Docker scoring) must exist; this is the "
        "go-ready signal for the first-PC test.",
    ),
    (
        "## 3. Docker path scoring (FIRST SCAFFOLD, NOT YET VALIDATED)",
        "Section 3 (Docker scoring) must exist AND must keep the "
        "FIRST SCAFFOLD / NOT YET VALIDATED honesty marker in "
        "its header so a casual reader does not misread the "
        "Docker path as validated.",
    ),
    (
        "## 4. Stop-condition handling",
        "Section 4 (stop-condition handling) must exist; reviewers "
        "must read 'where I stopped' BEFORE per-step diagnostics "
        "when any acceptance line is missing.",
    ),
    (
        "## 5. Outputs of the intake review",
        "Section 5 (review outputs) must exist; defines the three "
        "artefacts (intake summary line, BE task per failing line, "
        "doc-fix tasks) the reviewer produces.",
    ),
    (
        "## 6. What this checklist does NOT do",
        "Section 6 (negative-scope honesty section) must exist; "
        "explicitly disclaims that the checklist itself can flip "
        "FIRST SCAFFOLD / NOT YET VALIDATED markers.",
    ),
)


def test_required_top_level_headers_present(checklist_text: str):
    missing = [
        (header, msg)
        for header, msg in _REQUIRED_HEADERS
        if header not in checklist_text
    ]
    assert not missing, (
        "Load-bearing headers missing from "
        "first-pc-report-review-checklist.md:\n"
        + "\n".join(f"  - {h!r}: {msg}" for h, msg in missing)
    )


def test_section_ordering_is_canonical(checklist_text: str):
    """The six numbered sections must appear in 1..6 order so a
    reviewer reading top-to-bottom hits intake, then non-Docker,
    then Docker, then stop-conditions, then outputs, then the
    negative-scope disclaimer. Re-ordering would let a reviewer
    score Docker before confirming the non-Docker path is even
    green."""
    last = -1
    for header, _msg in _REQUIRED_HEADERS:
        idx = checklist_text.find(header)
        assert idx != -1, f"Missing header: {header!r}"
        assert idx > last, (
            f"Header {header!r} appears out of canonical order. "
            "Sections 1..6 are positionally locked; reordering "
            "breaks the reviewer flow."
        )
        last = idx


# 2. Stream tag + companion-doc cross-link


def test_stream_tag_present(checklist_text: str):
    assert (
        "BE-GO-READY-INSTALL-017-FIRST-PC-REPORT-INTAKE-CHECKLIST"
        in checklist_text
    ), (
        "Checklist must keep its INSTALL-017 stream tag so the "
        "audit trail back to the brief is preserved."
    )


def test_template_cross_links_to_checklist(template_text: str):
    """The report template must link to this checklist in its
    Companion docs block, otherwise a reviewer who only knows the
    template has no way to reach the intake-side artefact."""
    assert "first-pc-report-review-checklist.md" in template_text, (
        "first-pc-validation-report-template.md must cross-link "
        "to first-pc-report-review-checklist.md so the reviewer "
        "side is reachable from the operator side."
    )


def test_checklist_cross_links_back_to_template(checklist_text: str):
    """Symmetric: the checklist must link back to the template,
    otherwise a reviewer who lands on the checklist first cannot
    find the operator-facing template."""
    assert "first-pc-validation-report-template.md" in checklist_text, (
        "first-pc-report-review-checklist.md must cross-link "
        "back to first-pc-validation-report-template.md so the "
        "operator-facing artefact is reachable."
    )


# 3. Honesty contract -- Docker FIRST SCAFFOLD / NOT YET VALIDATED


def test_docker_section_keeps_first_scaffold_markers(checklist_text: str):
    """The Docker section header must keep both the FIRST SCAFFOLD
    and the NOT YET VALIDATED markers so the reviewer cannot
    misread the checklist as a validation claim. The brief is
    explicit: 'Keep Docker FIRST SCAFFOLD / NOT YET VALIDATED
    until a real report is green.'"""
    assert "FIRST SCAFFOLD" in checklist_text, (
        "first-pc-report-review-checklist.md must keep the literal "
        "`FIRST SCAFFOLD` honesty marker for the Docker path."
    )
    assert "NOT YET VALIDATED" in checklist_text, (
        "first-pc-report-review-checklist.md must keep the literal "
        "`NOT YET VALIDATED` honesty marker. The brief explicitly "
        "says: keep these markers until a real report is green."
    )


def test_checklist_does_not_claim_install_is_validated(checklist_text: str):
    """Negative lock: phrases that would imply the install is
    pre-validated must NOT appear in the checklist body. The
    checklist is the *review process*; it cannot itself be the
    validation."""
    forbidden_phrases = (
        # Bare claims that the install is validated. Mirror of the
        # template's negative lock so both sides stay agnostic.
        "install is validated",
        "Plenora-ETL is validated",
        "production-ready Linux install",
    )
    for needle in forbidden_phrases:
        assert needle not in checklist_text, (
            f"Forbidden validation claim {needle!r} found in the "
            "review checklist. The checklist is the review "
            "process; only the filled-in report can flip a path "
            "to validated."
        )


# 4. ASCII-only (per the install-doc family scope rule)


def test_checklist_is_ascii_only(checklist_text: str):
    non_ascii = [
        (i, ch) for i, ch in enumerate(checklist_text) if ord(ch) > 127
    ]
    assert not non_ascii, (
        "first-pc-report-review-checklist.md must be ASCII-only "
        "(per the BE-GO-READY-INSTALL family scope rule). "
        f"Found {len(non_ascii)} non-ASCII char(s); first at "
        f"offset {non_ascii[0][0]}: U+{ord(non_ascii[0][1]):04X}."
    )
