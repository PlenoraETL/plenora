"""Architectural lock for the Celery worker enqueue/dequeue smoke design.

BE-GO-READY-INSTALLATION-018-CELERY-WORKER-ENQUEUE-SMOKE-DESIGN.

This stream did NOT add a real enqueue/dequeue smoke step to
`.github/workflows/ci-cleanroom.yml`. The brief was explicit that
fragile sleep loops are not acceptable; a deterministic completion
signal exists at the primitive level (Redis result backend +
`AsyncResult.get(timeout=N)`) but the missing piece is a pure
no-op test-only task. Adding a runtime task module is app-code
work and belongs to a separate stream (INSTALLATION-019).

The purpose of this guard suite is to ensure the gap and its
closure path **cannot be silently forgotten**. Two failure modes:

  A. Someone removes or rewrites the design section in
     `docs/ops/install-non-docker.md` section 12.6 without
     replacing it with a real smoke - the guard fails because the
     canonical primitive name and the bounded-smoke pattern
     disappear from the docs.
  B. Someone adds a `cleanroom.ping` task or a workflow step
     that purports to close the gap but does it incorrectly
     (e.g. `ignore_result=True`, side-effecting body, sleep
     loops, missing teardown) - the guard fails because the
     contract documented in 12.6.2 is no longer locked.

The guard is intentionally split into "design present" assertions
(today's posture: PASS) and "implementation contract" assertions
that activate only when the implementation lands (today: skipped
with an explicit pytest.skip pointing at INSTALLATION-019).

Outside scope of this guard:
  - the implementation itself (lives in INSTALLATION-019);
  - the existing INSTALLATION-014 readiness step contracts (locked
    separately by `test_celery_cleanroom_readiness_guard.py`);
  - the install script packaging contract (locked by
    `test_install_script_packaging_guard.py`).
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOC = _REPO_ROOT / "docs" / "ops" / "install-non-docker.md"
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci-cleanroom.yml"
_CELERY_APP_PY = _REPO_ROOT / "backend" / "workers" / "celery_app.py"
_CLEANROOM_TASK_PY = (
    _REPO_ROOT / "backend" / "workers" / "tasks" / "cleanroom_task.py"
)

_DESIGN_SECTION_HEADER = (
    "### 12.6 Real worker enqueue/dequeue smoke (DESIGN, NOT IMPLEMENTED)"
)
_CANONICAL_TASK_NAME = "cleanroom.ping"
_NEXT_STREAM_TAG = (
    "BE-GO-READY-INSTALLATION-019-CELERY-WORKER-ENQUEUE-SMOKE-IMPL"
)


@pytest.fixture(scope="module")
def doc_text() -> str:
    return _DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def doc_section_12_6(doc_text: str) -> str:
    if _DESIGN_SECTION_HEADER not in doc_text:
        return ""
    start = doc_text.index(_DESIGN_SECTION_HEADER)
    # Section 12.6 ends at the next top-level `---` separator before
    # the `## 13. Related docs` header that follows.
    end_marker = "\n---\n\n## 13. Related docs"
    end = doc_text.find(end_marker, start)
    if end == -1:
        end = len(doc_text)
    return doc_text[start:end]


# =====================================================================
# Group A - "Design present" assertions (today: must PASS)
# =====================================================================


def test_design_section_12_6_exists(doc_text: str):
    assert _DESIGN_SECTION_HEADER in doc_text, (
        f"docs/ops/install-non-docker.md must contain section 12.6 "
        f"with header {_DESIGN_SECTION_HEADER!r}. Removing this "
        "section without replacing it with a real enqueue smoke "
        "silently closes the gap and leaves the cleanroom blind to "
        "worker enqueue regressions."
    )


def test_design_section_names_canonical_primitive(doc_section_12_6: str):
    assert _CANONICAL_TASK_NAME in doc_section_12_6, (
        f"Section 12.6 must name the canonical primitive "
        f"{_CANONICAL_TASK_NAME!r}. Renaming the primitive in docs "
        "without coordinating the implementation stream "
        "(INSTALLATION-019) breaks the closure path."
    )


def test_design_section_documents_bounded_smoke_pattern(
    doc_section_12_6: str,
):
    # The four invariants of the smoke pattern (no sleep loops,
    # bounded timeout, --pool=solo for determinism, AsyncResult.get
    # for the completion signal) must all appear in 12.6 so a future
    # implementer cannot drift to a fragile pattern.
    expectations = (
        ("AsyncResult.get(timeout=", "Section 12.6 must document the bounded AsyncResult.get(timeout=N) pattern."),
        ("--pool=solo", "Section 12.6 must document --pool=solo to avoid prefork complexity in the smoke."),
        ("sleep-free", "Section 12.6 must explicitly state the smoke is sleep-free."),
        ("ignore_result=False", "Section 12.6 must require ignore_result=False on the primitive task."),
    )
    for needle, message in expectations:
        assert needle in doc_section_12_6, message


def test_design_section_calls_out_missing_primitive_explicitly(
    doc_section_12_6: str,
):
    assert "Blocker" in doc_section_12_6 or "blocker" in doc_section_12_6, (
        "Section 12.6 must contain a Blocker / blocker subsection "
        "naming the missing primitive. Without an explicit blocker "
        "section the gap is harder to triage when the next stream "
        "is scheduled."
    )
    assert "no test-only" in doc_section_12_6 or "no-op task" in doc_section_12_6, (
        "Section 12.6 must explain that no test-only / no-op task "
        "exists today (this is the actual blocker, not a CI infra "
        "gap)."
    )


def test_design_section_points_at_next_implementation_stream(
    doc_section_12_6: str,
):
    assert _NEXT_STREAM_TAG in doc_section_12_6, (
        f"Section 12.6 must reference the next implementation "
        f"stream tag {_NEXT_STREAM_TAG!r} so the closure path is "
        "discoverable from the docs alone."
    )


def test_design_section_is_ascii_only(doc_section_12_6: str):
    non_ascii = [
        (i, ch) for i, ch in enumerate(doc_section_12_6)
        if ord(ch) > 127
    ]
    assert not non_ascii, (
        f"Section 12.6 must be ASCII-only (per scope rule). "
        f"Found {len(non_ascii)} non-ASCII char(s); first at offset "
        f"{non_ascii[0][0]}: U+{ord(non_ascii[0][1]):04X}."
    )


# =====================================================================
# Group B - "Workflow does NOT silently claim closure" (today: must PASS)
# =====================================================================


def test_workflow_does_not_silently_close_gap_without_real_smoke():
    # If a future PR adds a step named like "Verify Celery worker
    # enqueue smoke" or "Verify Celery enqueue" to ci-cleanroom.yml,
    # the implementation stream (INSTALLATION-019) MUST also flip
    # the design guard - by adding the cleanroom_task.py file and
    # updating this test to require the implementation contract.
    # The check below trips when the workflow gains an enqueue step
    # but the runtime task file is still missing - that is exactly
    # the "silent close" failure mode we want to catch.
    workflow_text = _WORKFLOW.read_text(encoding="utf-8")
    has_enqueue_step = (
        "Verify Celery worker enqueue smoke" in workflow_text
        or "Verify Celery enqueue smoke" in workflow_text
        or "send_task(\"cleanroom.ping\")" in workflow_text
    )
    task_file_exists = _CLEANROOM_TASK_PY.is_file()
    assert has_enqueue_step == task_file_exists, (
        "Inconsistent state between ci-cleanroom.yml enqueue step "
        f"({has_enqueue_step}) and "
        f"{_CLEANROOM_TASK_PY.relative_to(_REPO_ROOT)} presence "
        f"({task_file_exists}). The workflow step and the runtime "
        "task module must land together in INSTALLATION-019; one "
        "without the other silently breaks the smoke."
    )


# =====================================================================
# Group C - "Implementation contract" (today: skipped; activates with 019)
# =====================================================================


@pytest.fixture(scope="module")
def cleanroom_task_text() -> str:
    if not _CLEANROOM_TASK_PY.is_file():
        pytest.skip(
            f"{_CLEANROOM_TASK_PY.relative_to(_REPO_ROOT)} is absent. "
            "Implementation belongs to INSTALLATION-019 "
            "(see docs/ops/install-non-docker.md section 12.6.3). "
            "When this file is added, this test fixture activates "
            "automatically and locks the contract from 12.6.2."
        )
    return _CLEANROOM_TASK_PY.read_text(encoding="utf-8")


def test_cleanroom_task_is_pure_noop(cleanroom_task_text: str):
    # The task module MUST be free of side-effecting imports. Any of
    # the following in the source means the task body could touch
    # DB / file / network / config / settings - violating the
    # "pure no-op" contract from section 12.6.2.
    forbidden_imports = (
        "from app.",
        "import app.",
        "from app ",
        "import app ",
        "import requests",
        "from requests",
        "import boto3",
        "from boto3",
        "open(",
        "Path(",
        "os.environ",
    )
    offending = []
    for needle in forbidden_imports:
        if needle in cleanroom_task_text:
            offending.append(needle)
    assert not offending, (
        f"workers/tasks/cleanroom_task.py must be a pure no-op. "
        f"Forbidden patterns found: {offending}. The task body must "
        "not touch DB, config, files, network, or environment - "
        "see install-non-docker.md section 12.6.2."
    )


def test_cleanroom_task_uses_canonical_name(cleanroom_task_text: str):
    assert f'name="{_CANONICAL_TASK_NAME}"' in cleanroom_task_text, (
        f"workers/tasks/cleanroom_task.py must register the task "
        f"with name={_CANONICAL_TASK_NAME!r}. The smoke step in "
        "ci-cleanroom.yml sends to this exact name."
    )


def test_cleanroom_task_does_not_ignore_result(cleanroom_task_text: str):
    assert "ignore_result=False" in cleanroom_task_text, (
        "workers/tasks/cleanroom_task.py must declare "
        "ignore_result=False explicitly. AsyncResult.get(...) "
        "cannot observe completion of an ignored-result task - "
        "the smoke would silently pass without verifying anything."
    )
    assert "ignore_result=True" not in cleanroom_task_text, (
        "ignore_result=True is incompatible with the bounded smoke "
        "pattern in install-non-docker.md section 12.6.1."
    )


def test_celery_app_includes_cleanroom_task_module(cleanroom_task_text: str):
    # The fixture above already ensures cleanroom_task.py exists.
    # Once it exists, workers/celery_app.py `include=[...]` MUST
    # register it - otherwise the worker process never loads the
    # task and `send_task("cleanroom.ping")` raises NotRegistered.
    celery_app_text = _CELERY_APP_PY.read_text(encoding="utf-8")
    assert '"workers.tasks.cleanroom_task"' in celery_app_text, (
        "workers/celery_app.py include=[...] must list "
        "'workers.tasks.cleanroom_task'. Without it the smoke "
        "step in ci-cleanroom.yml fails with NotRegistered."
    )
