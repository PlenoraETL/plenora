"""
Architectural lock for the Celery worker enqueue/dequeue smoke.

BE-GO-READY-INSTALLATION-019-CELERY-WORKER-ENQUEUE-SMOKE-IMPL
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests2.shared._source_scan import python_code_only as _python_code_only


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOC = _REPO_ROOT / "docs" / "ops" / "install-non-docker.md"
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci-cleanroom.yml"
_CELERY_APP_PY = _REPO_ROOT / "backend" / "workers" / "celery_app.py"
_CLEANROOM_TASK_PY = (
    _REPO_ROOT / "backend" / "workers" / "tasks" / "cleanroom_task.py"
)

_DESIGN_SECTION_HEADER = (
    "### 12.6 Real worker enqueue/dequeue smoke (IMPLEMENTED)"
)
_CANONICAL_TASK_NAME = "cleanroom.ping"
_IMPL_STREAM_TAG = (
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
    # Section 12.6 ends at the next 12.x sub-section (12.7 added by
    # INSTALLATION-020) or, if absent, at the next H2 (which is now
    # `## 13.` after INSTALLATION-023 renumbered the related-docs
    # anchor to `## 14.`).
    end_marker = "\n### 12.7 "
    if end_marker not in doc_text[start:]:
        end_marker = "\n## "
    end = doc_text.find(end_marker, start)
    if end == -1:
        end = len(doc_text)
    return doc_text[start:end]


# Group A - Doc section 12.6 documents the bounded smoke (must PASS)


def test_design_section_12_6_exists(doc_text: str):
    assert _DESIGN_SECTION_HEADER in doc_text, (
        f"docs/ops/install-non-docker.md must contain section 12.6 "
        f"with header {_DESIGN_SECTION_HEADER!r}. The section is "
        "the contract surface for the implemented enqueue smoke; "
        "renaming or deleting it breaks the closure trail and "
        "leaves a future regression untriaged."
    )


def test_design_section_names_canonical_primitive(doc_section_12_6: str):
    assert _CANONICAL_TASK_NAME in doc_section_12_6, (
        f"Section 12.6 must name the canonical primitive "
        f"{_CANONICAL_TASK_NAME!r}. The CI workflow step sends to "
        "this exact name; renaming silently breaks the smoke."
    )


def test_design_section_documents_bounded_smoke_pattern(
    doc_section_12_6: str,
):
    # The four invariants of the smoke pattern (no sleep loops,
    # bounded timeout, --pool=solo for determinism, AsyncResult.get
    # for the completion signal) must remain locked in the docs so
    # a future PR cannot drift the implementation to a fragile
    # pattern.
    expectations = (
        ("AsyncResult.get(timeout=", "Section 12.6 must document the bounded AsyncResult.get(timeout=N) pattern."),
        ("--pool=solo", "Section 12.6 must document --pool=solo to avoid prefork complexity in the smoke."),
        ("sleep-free", "Section 12.6 must explicitly state the smoke is sleep-free."),
        ("ignore_result=False", "Section 12.6 must require ignore_result=False on the primitive task."),
    )
    for needle, message in expectations:
        assert needle in doc_section_12_6, message


def test_design_section_records_implementation_stream(
    doc_section_12_6: str,
):
    assert _IMPL_STREAM_TAG in doc_section_12_6, (
        f"Section 12.6 must reference the implementation stream "
        f"tag {_IMPL_STREAM_TAG!r} so the closure provenance is "
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


# Group B - "Workflow does NOT silently claim closure" (today: must PASS)


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


# Group C - "Implementation contract" (today: skipped; activates with 019)


@pytest.fixture(scope="module")
def cleanroom_task_text() -> str:
    assert _CLEANROOM_TASK_PY.is_file(), (
        f"{_CLEANROOM_TASK_PY.relative_to(_REPO_ROOT)} must exist - "
        "INSTALLATION-019 added this primitive. If it has been "
        "deleted, the workflow enqueue smoke step also has to be "
        "removed atomically (Group B asserts the IFF), and the "
        "rationale belongs in a new audit entry."
    )
    return _CLEANROOM_TASK_PY.read_text(encoding="utf-8")


def test_cleanroom_task_is_pure_noop(cleanroom_task_text: str):
    # The task module MUST be free of side-effecting imports. Any of
    # the following in the actual code (NOT docstrings or comments)
    # means the task body could touch DB / file / network / config /
    # settings - violating the "pure no-op" contract from
    # section 12.6.2.
    code_only = _python_code_only(cleanroom_task_text)
    forbidden_patterns = (
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
    offending = [p for p in forbidden_patterns if p in code_only]
    assert not offending, (
        f"workers/tasks/cleanroom_task.py must be a pure no-op. "
        f"Forbidden patterns found in code (excluding docstrings/"
        f"comments): {offending}. The task body must not touch DB, "
        "config, files, network, or environment - see "
        "install-non-docker.md section 12.6.2."
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
    code_only = _python_code_only(cleanroom_task_text)
    assert "ignore_result=True" not in code_only, (
        "ignore_result=True is incompatible with the bounded smoke "
        "pattern in install-non-docker.md section 12.6.1. "
        "(Mention in docstring is OK; only the actual decorator "
        "argument is forbidden.)"
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
