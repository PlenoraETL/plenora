"""Architectural lock for the cleanroom-vs-production Celery drift doc.

BE-GO-READY-INSTALLATION-022-CELERY-DRIFT-STORAGE-DEBT-GUARD.

Source-bound assertions on `docs/ops/install-non-docker.md`
section 12.8 (Cleanroom Celery smoke vs production drift). The
section is the contract surface that records the precise gap
between what `ci-cleanroom.yml` exercises and what
`plenora-celery.service` / `plenora-beat.service` actually run
in production. If the drift list is silently elided or weakened,
operators and reviewers can mistake a green cleanroom for a
green production change.

Locks today:

  - section header exists with the canonical title;
  - the four high-impact drift rows are named
    (pool, concurrency, beat interval, real domain task body);
  - the explicit "sanity check, not equivalence" wording is
    present so a reviewer cannot misread the section as a prod
    parity claim;
  - the future closure streams are named so the gap is
    discoverable;
  - section is ASCII-only.

Outside scope:
  - the cleanroom workflow itself (locked by
    `test_celery_cleanroom_readiness_guard.py`,
    `test_celery_enqueue_smoke_design_guard.py`,
    `test_celery_beat_tick_smoke_design_guard.py`);
  - the storage upload_intent debt doc (locked by
    `test_storage_upload_intent_debt_doc_guard.py`).
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOC = _REPO_ROOT / "docs" / "ops" / "install-non-docker.md"

_DRIFT_SECTION_HEADER = (
    "### 12.8 Cleanroom Celery smoke vs production drift "
    "(sanity check, NOT equivalence)"
)


@pytest.fixture(scope="module")
def doc_text() -> str:
    return _DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def drift_section(doc_text: str) -> str:
    if _DRIFT_SECTION_HEADER not in doc_text:
        return ""
    start = doc_text.index(_DRIFT_SECTION_HEADER)
    # Section ends at the next top-level `---` separator before
    # the `## 13. Related docs` header.
    end_marker = "\n---\n\n## 13. Related docs"
    end = doc_text.find(end_marker, start)
    if end == -1:
        end = len(doc_text)
    return doc_text[start:end]


def test_drift_section_12_8_exists(doc_text: str):
    assert _DRIFT_SECTION_HEADER in doc_text, (
        f"docs/ops/install-non-docker.md must contain section 12.8 "
        f"with header {_DRIFT_SECTION_HEADER!r}. The section is the "
        "contract that records cleanroom-vs-prod drift; deleting it "
        "lets reviewers misread a green cleanroom as production "
        "equivalence (which it is NOT)."
    )


def test_drift_section_declares_sanity_check_not_equivalence(
    drift_section: str,
):
    # The phrasing must remain explicit so a reader scanning the
    # header alone gets the contract.
    assert "sanity check, NOT equivalence" in drift_section or \
           "sanity check, not equivalence" in drift_section.lower(), (
        "Section 12.8 must explicitly state the cleanroom smoke is "
        "a 'sanity check, not equivalence'. Without this wording the "
        "section is ambiguous about whether a green CI = green prod."
    )
    # The 'necessary but not sufficient' sentence is the load-bearing
    # claim of the section. Soften-protect it: any phrasing that
    # contains 'necessary' AND 'not sufficient' or the canonical
    # bullet 'green cleanroom is necessary ... not sufficient' should
    # work; keep the assertion permissive on word order.
    assert (
        "necessary condition" in drift_section
        and "not a sufficient" in drift_section
    ), (
        "Section 12.8 must keep the 'necessary condition / not "
        "sufficient' framing so the deploy gate semantics are clear."
    )


def test_drift_section_lists_high_impact_drift_rows(drift_section: str):
    # Four drift items MUST be visible in the section text. Each
    # corresponds to a real production-vs-cleanroom divergence
    # documented in section 12.8.1.
    expectations = (
        ("prefork",
         "Section 12.8 must enumerate the prefork vs solo pool drift."),
        ("--pool=solo",
         "Section 12.8 must explicitly reference --pool=solo as the "
         "cleanroom-side choice."),
        ("--concurrency=4",
         "Section 12.8 must reference the production --concurrency=4 "
         "to anchor the concurrency drift item."),
        ("worker_process_init",
         "Section 12.8 must reference worker_process_init - the "
         "fork-time bootstrap path is the most consequential prefork-"
         "only behaviour."),
        ("max_interval",
         "Section 12.8 must reference max_interval (the 60s production "
         "beat tick vs 5s cleanroom interval)."),
    )
    for needle, message in expectations:
        assert needle in drift_section, message
    # The "real domain task body" row is the most consequential
    # drift item: cleanroom uses only pure no-op tasks. Match
    # case-insensitively so the doc can capitalize the table
    # cell without breaking the lock.
    assert "real domain task body" in drift_section.lower(), (
        "Section 12.8 must call out that real domain task bodies "
        "are NOT exercised in cleanroom (only cleanroom.ping / "
        "cleanroom.beat_ping pure no-ops)."
    )


def test_drift_section_classifies_drift_severity(drift_section: str):
    # The HIGH/MEDIUM/none classification keeps reviewers from
    # collapsing every row into a single bucket. Removing the
    # severity column would silently flatten the prioritization.
    assert "**HIGH**" in drift_section, (
        "Section 12.8 must classify drift rows with **HIGH** for "
        "prefork/concurrency/real-task-body so reviewers can tell "
        "those apart from no-drift rows."
    )
    assert "**MEDIUM**" in drift_section, (
        "Section 12.8 must classify drift rows with **MEDIUM** for "
        "beat-interval/catchup/retention paths."
    )


def test_drift_section_names_future_closure_streams(drift_section: str):
    expected_streams = (
        ("INSTALLATION-024-CELERY-PREFORK-WORKER-SMOKE",
         "Section 12.8 must name INSTALLATION-024 as the closure "
         "path for the HIGH prefork/concurrency drift."),
        ("INSTALLATION-025-CELERY-BEAT-RETENTION-TICK-SMOKE",
         "Section 12.8 must name INSTALLATION-025 as the closure "
         "path for the MEDIUM retention/catchup drift."),
    )
    for needle, message in expected_streams:
        assert needle in drift_section, message


def test_drift_section_lists_hard_non_goals(drift_section: str):
    # The 'Hard non-goals' list keeps future PRs from over-scoping
    # closure work into things that are intentionally NOT pursued
    # (no soak tests in CI; no replacement of the prod weekly
    # backup-test drill; no multi-host cluster smoke).
    assert "Hard non-goals" in drift_section, (
        "Section 12.8 must contain a 'Hard non-goals' subsection so "
        "future closure streams cannot accidentally re-scope into "
        "soak / cluster / replacing the prod backup drill."
    )
    assert "soak" in drift_section, (
        "Section 12.8 must explicitly disclaim long-running soak "
        "tests in CI."
    )


def test_drift_section_is_ascii_only(drift_section: str):
    non_ascii = [
        (i, ch) for i, ch in enumerate(drift_section)
        if ord(ch) > 127
    ]
    assert not non_ascii, (
        f"Section 12.8 must be ASCII-only (per scope rule). "
        f"Found {len(non_ascii)} non-ASCII char(s); first at offset "
        f"{non_ascii[0][0]}: U+{ord(non_ascii[0][1]):04X}."
    )
