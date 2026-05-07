"""
Architectural lock for the clean-install smoke harness.

BE-GO-READY-INSTALL-007-CLEAN-INSTALL-SMOKE-HARNESS.
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
_INSTALL_SH = _REPO_ROOT / "scripts" / "install.sh"


_HARNESS_HEADER = "## 5. Clean-install smoke harness (canonical sequence)"
_HARNESS_END_MARKER = "\n## 6. What is NOT in this dry-run pack"


@pytest.fixture(scope="module")
def doc_text() -> str:
    return _DRY_RUN.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def harness_section(doc_text: str) -> str:
    if _HARNESS_HEADER not in doc_text:
        return ""
    start = doc_text.index(_HARNESS_HEADER)
    end = doc_text.find(_HARNESS_END_MARKER, start)
    if end == -1:
        end = len(doc_text)
    return doc_text[start:end]


# Section 5 of the dry-run pack


def test_harness_section_exists(harness_section: str):
    assert harness_section, (
        "docs/ops/install-marco-dry-run.md must contain section 5 "
        f"with header {_HARNESS_HEADER!r}. Removing the canonical "
        "smoke harness leaves the eight verification items "
        "scattered across other docs and lets operators declare "
        "an install green while having skipped one of them."
    )
    # Stream tag must remain so future contributors can trace
    # the section back to the brief.
    assert "BE-GO-READY-INSTALL-007-CLEAN-INSTALL-SMOKE-HARNESS" \
        in harness_section, (
        "Section 5 must keep the BE-GO-READY-INSTALL-007 stream "
        "tag so the audit trail back to the brief is preserved."
    )


def test_harness_lists_eight_canonical_items(harness_section: str):
    # The eight harness items in the brief's canonical order.
    # Each one must appear as a recognizable phrase in section
    # 5.1 so the operator-facing mapping is complete.
    expected_items = (
        ("clone + env setup",
         "Section 5.1 must include the 'clone + env setup' "
         "harness item."),
        ("backend deps",
         "Section 5.1 must include the 'backend deps' (venv + "
         "pip install) harness item."),
        ("DB init + migrations",
         "Section 5.1 must include the 'DB init + migrations' "
         "harness item (schema autocreate on first uvicorn "
         "boot)."),
        ("object storage choice",
         "Section 5.1 must include the 'object storage choice' "
         "harness item."),
        ("workers/beat count",
         "Section 5.1 must include the 'workers/beat count' "
         "harness item (foreground for first run)."),
        ("health + ready checks",
         "Section 5.1 must include the 'health + ready checks' "
         "harness item."),
        ("memory caps visibility",
         "Section 5.1 must include the 'memory caps visibility' "
         "harness item - this is the new contract added by "
         "INSTALL-007."),
        ("tiny ETL/project smoke",
         "Section 5.1 must include the 'tiny ETL/project smoke' "
         "harness item - locks reuse of the existing "
         "cleanroom.ping primitive."),
    )
    for needle, message in expected_items:
        assert needle in harness_section, message


def test_harness_5_2_lists_canonical_memory_caps(harness_section: str):
    # The six canonical memory caps that govern the runtime
    # memory budget. All six MUST appear in 5.2 - dropping any
    # of them lets an operator declare a small-PC install green
    # without verifying the cap that would actually trigger.
    expected_caps = (
        "LOAD_FILE_MAX_BYTES",
        "WORKER_RSS_MAX_BYTES",
        "RESOURCE_MAX_BYTES",
        "ARTIFACT_PREVIEW_MAX_BYTES",
        "CROSS_JOIN_MAX_OUTPUT_ROWS",
        "MULTI_JOIN_MAX_OUTPUT_ROWS",
    )
    for cap in expected_caps:
        assert cap in harness_section, (
            f"Section 5.2 must list memory cap {cap!r} so the "
            "operator can verify the runtime-effective value "
            "before the foreground smoke. Dropping a cap here "
            "means the corresponding guard is invisible to the "
            "operator at install time."
        )


def test_install_sh_smoke_lists_same_memory_caps():
    """The executable `install.sh --smoke` path must print the same
    caps as the operator-facing section 5.2 one-liner. Otherwise the
    docs and command drift immediately after a new cap lands."""
    script = _INSTALL_SH.read_text(encoding="utf-8")
    expected_caps = (
        "LOAD_FILE_MAX_BYTES",
        "WORKER_RSS_MAX_BYTES",
        "RESOURCE_MAX_BYTES",
        "ARTIFACT_PREVIEW_MAX_BYTES",
        "CROSS_JOIN_MAX_OUTPUT_ROWS",
        "MULTI_JOIN_MAX_OUTPUT_ROWS",
    )
    assert "INSTALLER_SMOKE_MEMORY_CAPS=(" in script, (
        "scripts/install.sh --smoke must keep a visible "
        "INSTALLER_SMOKE_MEMORY_CAPS array."
    )
    for cap in expected_caps:
        assert cap in script, (
            f"scripts/install.sh --smoke must print memory cap {cap!r}, "
            "matching docs/ops/install-marco-dry-run.md section 5.2."
        )


def test_harness_5_2_uses_canonical_settings_accessor(
    harness_section: str,
):
    # The visibility one-liner MUST use the canonical
    # `app.config.settings.get(...)` accessor. A future edit
    # that switches to `os.getenv` would silently bypass the
    # `Settings.DEFAULTS` fallback and report None for caps the
    # operator did not explicitly override.
    assert "from app.config import settings" in harness_section, (
        "Section 5.2 must import `settings` from `app.config` so "
        "the visibility one-liner reflects what the runtime will "
        "actually enforce (defaults + env override)."
    )
    assert "settings.get(" in harness_section, (
        "Section 5.2 must call `settings.get(KEY)` so DEFAULTS "
        "fallback applies. Switching to `os.getenv(KEY)` would "
        "report None for unset caps and mislead the operator."
    )


def test_harness_5_3_uses_cleanroom_ping(harness_section: str):
    # The tiny ETL smoke MUST reuse the existing pure no-op task
    # `cleanroom.ping`. Inventing a new task here would either
    # require new production code OR depend on real domain state
    # the first PC test does not have. Both are out of scope.
    assert "cleanroom.ping" in harness_section, (
        "Section 5.3 must invoke the existing `cleanroom.ping` "
        "task (defined in backend/workers/tasks/cleanroom_task.py "
        "and registered via the `include=[...]` list of "
        "workers/celery_app.py). It is the canonical, "
        "deterministic, sleep-free smoke primitive shared with "
        "the cleanroom CI lane."
    )
    # The bounded primitive MUST be `AsyncResult.get(timeout=...)`
    # - NOT a shell sleep loop or `time.sleep(...)`.
    assert "AsyncResult.get(timeout=" in harness_section or \
           "ar.get(timeout=" in harness_section, (
        "Section 5.3 must use the bounded `AsyncResult.get("
        "timeout=N)` primitive (NOT a sleep loop). This is the "
        "same pattern the cleanroom CI workflow uses."
    )
    # The expected output is `pong` - lock the contract.
    assert "pong" in harness_section, (
        "Section 5.3 must show `pong` as the expected output of "
        "`cleanroom.ping`. A future change that broke the task "
        "body would silently slip past a renamed expected value."
    )


def test_harness_5_4_documents_docker_deltas(harness_section: str):
    # The Docker-vs-single-machine delta table is the brief's
    # explicit ask. Each of the eight harness rows must have a
    # delta column so an operator with Docker can map each row
    # to the future Docker artifact (or to "today: NOT
    # IMPLEMENTED").
    assert (
        "Docker vs single-machine deltas" in harness_section
        or "Docker path (target shape only)" in harness_section
    ), (
        "Section 5.4 must contain the Docker-vs-single-machine "
        "delta table. Without it, an operator with Docker on the "
        "PC has no map from the harness rows to the future "
        "Docker artifacts."
    )
    # The delta table MUST keep `RESOURCE_MAX_BYTES` (and
    # siblings) visible on both paths - this is the brief's
    # explicit memory-caps visibility ask.
    assert "RESOURCE_MAX_BYTES" in harness_section, (
        "Section 5.4 must keep RESOURCE_MAX_BYTES visible in the "
        "delta table so the cap surface stays explicit on both "
        "Docker and single-machine paths."
    )
    # The Docker honesty disclaimer MUST stay in the deltas
    # section - removing it would let a reader infer the Docker
    # path is validated today. INSTALL-011 flipped the canonical
    # honesty marker from "NOT IMPLEMENTED" / "NO Dockerfile" to
    # "FIRST SCAFFOLD" + "NOT YET VALIDATED"; either form keeps
    # the disclaimer.
    assert (
        "FIRST SCAFFOLD" in harness_section
        or "NOT YET VALIDATED" in harness_section
        or "NO `Dockerfile`" in harness_section
        or "NOT IMPLEMENTED" in harness_section
        or "no `Dockerfile`" in harness_section.lower()
    ), (
        "Section 5.4 must keep the honesty disclaimer that the "
        "Docker path is FIRST SCAFFOLD / NOT YET VALIDATED. "
        "Without "
        "it the delta table reads as if both paths exist today."
    )


def test_harness_section_5_referenced_canonical_install_locations(
    harness_section: str,
):
    # The harness must point operators back to the canonical
    # locations for memory-caps overrides and the Docker target
    # shape, so a single-page reader does not silently lose the
    # context.
    assert "install-non-docker.md" in harness_section, (
        "Section 5 must cross-link to install-non-docker.md "
        "(specifically the recommended-values memory caps "
        "section) so the small-PC override values are reachable."
    )
    assert "install-docker.md" in harness_section, (
        "Section 5.4 must cross-link to install-docker.md so the "
        "Docker target shape is one click away from the delta "
        "table."
    )


def test_harness_section_is_ascii_only(harness_section: str):
    non_ascii = [
        (i, ch) for i, ch in enumerate(harness_section)
        if ord(ch) > 127
    ]
    assert not non_ascii, (
        f"Section 5 of the dry-run pack must be ASCII-only "
        f"(per scope rule). Found {len(non_ascii)} non-ASCII "
        f"char(s); first at offset {non_ascii[0][0]}: "
        f"U+{ord(non_ascii[0][1]):04X}."
    )


def test_section_ordering_unchanged(doc_text: str):
    # The native-deps guard test extracts section 3 by anchoring
    # the end at `\n## 4. Failure triage checklist`. If a future
    # edit renumbers section 4, that lock breaks. Assert the
    # canonical ordering 1..7 explicitly so a section-number
    # rename surfaces here, not as a cryptic failure in the
    # native-deps test.
    expected_in_order = (
        "## 0. First-PC handoff pack (entry point)",
        "## 1. Manual choices",
        "## 2. Exact command sequence",
        "## 3. Native dependency verification",
        "## 4. Failure triage checklist",
        "## 5. Clean-install smoke harness",
        "## 6. What is NOT in this dry-run pack",
        "## 7. Related docs",
    )
    last = -1
    for header in expected_in_order:
        idx = doc_text.find(header)
        assert idx != -1, (
            f"docs/ops/install-marco-dry-run.md must contain "
            f"section header {header!r}. The seven sections form "
            "the canonical structure of the dry-run pack."
        )
        assert idx > last, (
            f"Section header {header!r} appears out of order. "
            "The dry-run pack section ordering is locked because "
            "adjacent guard tests (native-deps, this guard) "
            "anchor on section-number transitions."
        )
        last = idx
