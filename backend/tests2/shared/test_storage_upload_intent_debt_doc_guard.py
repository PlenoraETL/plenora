"""
Architectural lock for the upload_intent debt classification.

BE-GO-READY-INSTALLATION-022-CELERY-DRIFT-STORAGE-DEBT-GUARD
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_AUDIT = _REPO_ROOT / ".agent-temp" / "BE_STORAGE_ENGINE_AUDIT.md"

_DEBT_SECTION_HEADER = (
    "## 22.1 upload_intent as accepted architectural debt"
)
_TARGET_A_STREAM = (
    "BE-GO-READY-STORAGE-ENGINE-030-PRESIGNED-UPLOAD-EVENT-DRIVEN"
)
_TARGET_B_STREAM = (
    "BE-GO-READY-STORAGE-ENGINE-031-TRANSACTIONAL-OUTBOX-UPLOAD"
)


@pytest.fixture(scope="module")
def audit_text() -> str:
    return _AUDIT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def debt_section(audit_text: str) -> str:
    if _DEBT_SECTION_HEADER not in audit_text:
        return ""
    start = audit_text.index(_DEBT_SECTION_HEADER)
    # Section ends at the next `## 22.2` (next H2 inside the same
    # incoming-from-BE block) or end-of-file.
    end_marker = "\n## 22.2 What did NOT change"
    end = audit_text.find(end_marker, start)
    if end == -1:
        end = len(audit_text)
    return audit_text[start:end]


def test_debt_section_22_1_exists(audit_text: str):
    assert _DEBT_SECTION_HEADER in audit_text, (
        f".agent-temp/BE_STORAGE_ENGINE_AUDIT.md must contain "
        f"section 22.1 with header {_DEBT_SECTION_HEADER!r}. "
        "Removing or rewriting this section silently closes the "
        "architectural debt classification - the W2 race "
        "mitigation today (record_pending(operation='upload_intent'))"
        " is intentionally NOT the final architecture."
    )


def test_debt_section_names_upload_intent_canonical(debt_section: str):
    assert "upload_intent" in debt_section, (
        "Section 22.1 must name `upload_intent` explicitly - the "
        "canonical primitive identifier the runtime uses in "
        "`record_pending(operation='upload_intent')`. Renaming the "
        "doc without renaming runtime breaks the debt-trail link."
    )
    assert "operation=\"upload_intent\"" in debt_section or \
           "operation='upload_intent'" in debt_section, (
        "Section 22.1 must include the verbatim "
        "`operation=\"upload_intent\"` shape so the runtime call "
        "signature is anchored to the doc."
    )


def test_debt_section_documents_both_target_architectures(
    debt_section: str,
):
    # Target A: presigned URL + object event (S3 EventBridge / SQS).
    assert "Presigned upload URL" in debt_section, (
        "Section 22.1 must document the 'Presigned upload URL + "
        "object event' target architecture (Target A) so the future "
        "stream STORAGE-ENGINE-030 has a concrete spec to land "
        "against."
    )
    for keyword in ("EventBridge", "SQS", "presigned"):
        assert keyword in debt_section, (
            f"Section 22.1 must reference '{keyword}' in the Target A "
            "description (presigned upload + cloud event integration)."
        )
    # Target B: transactional outbox.
    assert "Transactional outbox" in debt_section, (
        "Section 22.1 must document the 'Transactional outbox' "
        "target architecture (Target B) so the future stream "
        "STORAGE-ENGINE-031 has a concrete spec to land against."
    )
    assert "dispatcher" in debt_section, (
        "Section 22.1 must reference the dispatcher role in the "
        "Target B description (server writes outbox; dispatcher "
        "consumes it)."
    )


def test_debt_section_status_flag_is_accepted_mitigated(
    debt_section: str,
):
    # Both phrases must be present together: accepted+mitigated for
    # current state, debt-not-target for direction. Removing either
    # would let the section become ambiguous about closure status.
    assert "Accepted, mitigated" in debt_section or \
           "accepted, mitigated" in debt_section, (
        "Section 22.1 must classify the current state as "
        "'Accepted, mitigated' so reviewers see the operational "
        "posture explicitly."
    )
    assert "debt" in debt_section.lower(), (
        "Section 22.1 must classify the current state as debt - "
        "the contract is 'mitigated AND debt', not 'mitigated AND "
        "done'."
    )
    assert "Not yet committed" in debt_section, (
        "Section 22.1 must declare 'Not yet committed' for the "
        "final architecture - target A vs B is a product decision "
        "that has NOT been made."
    )


def test_debt_section_names_both_closure_streams(debt_section: str):
    assert _TARGET_A_STREAM in debt_section, (
        f"Section 22.1 must reference closure stream "
        f"{_TARGET_A_STREAM!r} so the future implementation path "
        "for Target A is discoverable from the docs alone."
    )
    assert _TARGET_B_STREAM in debt_section, (
        f"Section 22.1 must reference closure stream "
        f"{_TARGET_B_STREAM!r} so the future implementation path "
        "for Target B is discoverable from the docs alone."
    )


def test_debt_section_preserves_hard_non_goal(debt_section: str):
    # The PG-backed reconciler stays as the safety net even after
    # either target lands. Eliding this would let a future stream
    # propose ripping out the safety net while migrating - which
    # would re-open every W2 race during the migration window.
    assert "no replacement of the existing PG-backed reconciler" in \
           debt_section, (
        "Section 22.1 must preserve the hard non-goal 'no "
        "replacement of the existing PG-backed reconciler'. The "
        "PG reconciler stays as the safety net regardless of "
        "target A vs B."
    )


def test_debt_section_lists_concrete_debt_reasons(debt_section: str):
    # The five reasons in 22.1.2 are the load-bearing rationale for
    # why this is debt. Eliding any single one would let a future
    # PR silently disagree with the rationale by removing the row
    # from the doc instead of arguing against it.
    expected_phrases = (
        ("DB participates in every upload",
         "Section 22.1.2 must keep the DB-on-hot-path debt reason."),
        ("Polling reconciler",
         "Section 22.1.2 must keep the polling reconciler debt "
         "reason (15-min default interval)."),
        ("PG",
         "Section 22.1.2 must reference PG as the coupling site."),
        ("backpressure",
         "Section 22.1.2 must keep the no-backpressure debt reason."),
        ("request thread",
         "Section 22.1.2 must keep the upload-in-request-thread "
         "debt reason."),
    )
    for needle, message in expected_phrases:
        assert needle in debt_section, message


def test_debt_section_is_ascii_only(debt_section: str):
    non_ascii = [
        (i, ch) for i, ch in enumerate(debt_section)
        if ord(ch) > 127
    ]
    assert not non_ascii, (
        f"Section 22.1 must be ASCII-only (per scope rule). "
        f"Found {len(non_ascii)} non-ASCII char(s); first at offset "
        f"{non_ascii[0][0]}: U+{ord(non_ascii[0][1]):04X}."
    )
