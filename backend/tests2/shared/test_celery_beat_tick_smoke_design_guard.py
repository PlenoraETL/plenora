"""Architectural lock for the Celery beat tick smoke design.

BE-GO-READY-INSTALLATION-020-CELERY-BEAT-TICK-SMOKE-DESIGN-FIRST-GUARD.

This stream did NOT add a real beat tick smoke step to
`.github/workflows/ci-cleanroom.yml`. The brief was explicit on
three constraints (no fragile sleep loops, no broad production
schedule seeding, no Docker artifacts), and the audit confirmed
that no beat-fired Celery task today has `ignore_result=False`.
The only existing beat-fired primitive (`beat_heartbeat`) is
`ignore_result=True` and not even wired through the production
`PlenoraBeatScheduler.tick()` (the heartbeat write happens via
the in-process `_write_heartbeat()` method).

Implementing a deterministic beat tick smoke therefore requires
a new app-side primitive (`cleanroom.beat_ping` task + env-gated
`beat_schedule` wiring) which belongs to a separate stream
(INSTALLATION-021).

This guard suite locks the gap so it cannot be silently forgotten
or silently closed. Three groups, mirroring the
INSTALLATION-018 pattern:

  Group A - design section 12.7 of `install-non-docker.md`
  exists with the canonical primitive name, the bounded smoke
  pattern invariants, and the closure stream tag.

  Group B - workflow step (when added) and runtime task module
  (when added) must land together. Any "one without the other"
  is silent gap-closure failure mode.

  Group C - implementation contract that activates only when the
  primitive lands. Today: skipped with explicit pytest.skip
  pointing at INSTALLATION-021.

Outside scope:
  - the implementation itself (lives in INSTALLATION-021);
  - the existing INSTALLATION-014 readiness step contract;
  - the existing INSTALLATION-019 enqueue smoke contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOC = _REPO_ROOT / "docs" / "ops" / "install-non-docker.md"
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci-cleanroom.yml"
_CELERY_APP_PY = _REPO_ROOT / "backend" / "workers" / "celery_app.py"
_BEAT_TASK_PY = (
    _REPO_ROOT / "backend" / "workers" / "tasks" / "cleanroom_beat_task.py"
)

_DESIGN_SECTION_HEADER = (
    "### 12.7 Real beat tick smoke (DESIGN, NOT IMPLEMENTED)"
)
_CANONICAL_TASK_NAME = "cleanroom.beat_ping"
_NEXT_STREAM_TAG = (
    "BE-GO-READY-INSTALLATION-021-CELERY-BEAT-TICK-SMOKE-IMPL"
)
_OPT_IN_ENV = "PLENORA_CLEANROOM_BEAT_PING"


@pytest.fixture(scope="module")
def doc_text() -> str:
    return _DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def doc_section_12_7(doc_text: str) -> str:
    if _DESIGN_SECTION_HEADER not in doc_text:
        return ""
    start = doc_text.index(_DESIGN_SECTION_HEADER)
    # Section 12.7 ends at the next top-level `---` separator
    # before the `## 13. Related docs` header.
    # Section 12.7 ends at the next 12.x sub-section (12.8 added by
    # INSTALLATION-022) or, if absent, at the next H2.
    end_marker = "\n### 12.8 "
    if end_marker not in doc_text[start:]:
        end_marker = "\n## "
    end = doc_text.find(end_marker, start)
    if end == -1:
        end = len(doc_text)
    return doc_text[start:end]


# =====================================================================
# Group A - Design section present (today: must PASS)
# =====================================================================


def test_design_section_12_7_exists(doc_text: str):
    assert _DESIGN_SECTION_HEADER in doc_text, (
        f"docs/ops/install-non-docker.md must contain section 12.7 "
        f"with header {_DESIGN_SECTION_HEADER!r}. The section is "
        "the contract surface for the (still-missing) beat tick "
        "smoke; deleting it without delivering the smoke silently "
        "closes the gap and leaves the cleanroom blind to beat "
        "scheduler regressions."
    )


def test_design_section_names_canonical_primitive(doc_section_12_7: str):
    assert _CANONICAL_TASK_NAME in doc_section_12_7, (
        f"Section 12.7 must name the canonical primitive "
        f"{_CANONICAL_TASK_NAME!r}. The future CI workflow step "
        "will send to this exact name; renaming the primitive in "
        "docs without coordinating INSTALLATION-021 breaks the "
        "closure path."
    )


def test_design_section_calls_out_in_process_heartbeat_finding(
    doc_section_12_7: str,
):
    # The audit insight that `_write_heartbeat()` is in-process
    # (not a Celery task) is the load-bearing reason this stream is
    # design-only. Removing it from the doc would erase the
    # rationale for the closure path.
    assert "_write_heartbeat" in doc_section_12_7, (
        "Section 12.7 must reference _write_heartbeat - the audit "
        "finding that the production beat heartbeat runs in-process "
        "(not as a Celery task) is the reason the standard "
        "AsyncResult-based smoke pattern does not apply directly."
    )
    assert (
        "ignore_result=True" in doc_section_12_7
        or "in-process" in doc_section_12_7
    ), (
        "Section 12.7 must explain WHY the existing primitives are "
        "unsuitable: beat_heartbeat is ignore_result=True AND the "
        "in-process write is not a Celery task."
    )


def test_design_section_documents_bounded_smoke_pattern(
    doc_section_12_7: str,
):
    expectations = (
        ("PLENORA_CLEANROOM_BEAT_PING",
         "Section 12.7 must require an env-gated opt-in to "
         "prevent production beat from firing the cleanroom task."),
        ("ignore_result=False",
         "Section 12.7 must require ignore_result=False on the "
         "primitive task."),
        ("--pool=solo",
         "Section 12.7 must reference --pool=solo (the worker side "
         "from 019 must remain compatible)."),
        ("trap",
         "Section 12.7 must document trap-based teardown so the "
         "beat process is killed on success or failure (no leaked "
         "background processes)."),
    )
    for needle, message in expectations:
        assert needle in doc_section_12_7, message


def test_design_section_explains_why_separate_stream(
    doc_section_12_7: str,
):
    assert "broad production schedule seeding" in doc_section_12_7 or \
           "schedules` table" in doc_section_12_7 or \
           "separate stream" in doc_section_12_7, (
        "Section 12.7 must explain WHY beat tick smoke cannot land "
        "in 019 (banned: broad production schedule seeding; banned: "
        "fragile sleep loops; the only available primitive is the "
        "in-process heartbeat which is not Celery-observable)."
    )


def test_design_section_points_at_next_implementation_stream(
    doc_section_12_7: str,
):
    assert _NEXT_STREAM_TAG in doc_section_12_7, (
        f"Section 12.7 must reference the next implementation "
        f"stream tag {_NEXT_STREAM_TAG!r} so the closure path is "
        "discoverable from the docs alone."
    )


def test_design_section_is_ascii_only(doc_section_12_7: str):
    non_ascii = [
        (i, ch)
        for i, ch in enumerate(doc_section_12_7)
        if ord(ch) > 127
    ]
    assert not non_ascii, (
        f"Section 12.7 must be ASCII-only (per scope rule). "
        f"Found {len(non_ascii)} non-ASCII char(s); first at offset "
        f"{non_ascii[0][0]}: U+{ord(non_ascii[0][1]):04X}."
    )


# =====================================================================
# Group B - Silent-close guard (today: must PASS)
# =====================================================================


def test_workflow_does_not_silently_close_gap_without_beat_primitive():
    # If a future PR adds a beat tick step to ci-cleanroom.yml or a
    # cleanroom_beat_task.py module, the OTHER must land in the
    # same change. Otherwise:
    # - workflow with no task module -> NotRegistered at runtime,
    #   smoke fails or is silently broken;
    # - task module with no workflow step -> dead code in production.
    workflow_text = _WORKFLOW.read_text(encoding="utf-8")
    has_beat_step = (
        "Verify Celery beat tick smoke" in workflow_text
        or _CANONICAL_TASK_NAME in workflow_text
        or _OPT_IN_ENV in workflow_text
    )
    task_file_exists = _BEAT_TASK_PY.is_file()
    assert has_beat_step == task_file_exists, (
        "Inconsistent state between ci-cleanroom.yml beat step "
        f"({has_beat_step}) and "
        f"{_BEAT_TASK_PY.relative_to(_REPO_ROOT)} presence "
        f"({task_file_exists}). The workflow step and the runtime "
        "task module must land together in INSTALLATION-021; one "
        "without the other silently breaks the smoke."
    )


# =====================================================================
# Group C - Implementation contract (today: skipped; activates with 021)
# =====================================================================


@pytest.fixture(scope="module")
def beat_task_text() -> str:
    if not _BEAT_TASK_PY.is_file():
        pytest.skip(
            f"{_BEAT_TASK_PY.relative_to(_REPO_ROOT)} is absent. "
            "Implementation belongs to INSTALLATION-021 "
            "(see docs/ops/install-non-docker.md section 12.7.3). "
            "When this file is added, this fixture activates "
            "automatically and locks the contract from 12.7.2."
        )
    return _BEAT_TASK_PY.read_text(encoding="utf-8")


def _python_code_only(source: str) -> str:
    """Strip string literals and comments via tokenize so docstring
    text mentioning forbidden patterns does not false-positive.
    Same helper as test_celery_enqueue_smoke_design_guard.py."""
    import io
    import tokenize

    out = []
    try:
        tokens = list(
            tokenize.generate_tokens(io.StringIO(source).readline)
        )
    except tokenize.TokenizeError:
        return source
    for tok in tokens:
        if tok.type in (tokenize.STRING, tokenize.COMMENT):
            continue
        out.append(tok.string)
    return " ".join(out)


def test_beat_task_is_pure_noop(beat_task_text: str):
    code_only = _python_code_only(beat_task_text)
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
        f"workers/tasks/cleanroom_beat_task.py must be a pure "
        f"no-op. Forbidden patterns found in code (excluding "
        f"docstrings/comments): {offending}. The task body must "
        "not touch DB, config, files, network, or environment - "
        "see install-non-docker.md section 12.7.2."
    )


def test_beat_task_uses_canonical_name(beat_task_text: str):
    assert f'name="{_CANONICAL_TASK_NAME}"' in beat_task_text, (
        f"workers/tasks/cleanroom_beat_task.py must register the "
        f"task with name={_CANONICAL_TASK_NAME!r}. The smoke step "
        "in ci-cleanroom.yml will send to this exact name."
    )


def test_beat_task_does_not_ignore_result(beat_task_text: str):
    assert "ignore_result=False" in beat_task_text, (
        "workers/tasks/cleanroom_beat_task.py must declare "
        "ignore_result=False explicitly. AsyncResult.get / backend "
        "polling cannot observe completion of an ignored-result "
        "task - the smoke would silently pass without verifying "
        "anything."
    )
    code_only = _python_code_only(beat_task_text)
    assert "ignore_result=True" not in code_only, (
        "ignore_result=True is incompatible with the bounded smoke "
        "pattern in install-non-docker.md section 12.7.5."
    )


def test_celery_app_includes_beat_task_module(beat_task_text: str):
    celery_app_text = _CELERY_APP_PY.read_text(encoding="utf-8")
    assert '"workers.tasks.cleanroom_beat_task"' in celery_app_text, (
        "workers/celery_app.py include=[...] must list "
        "'workers.tasks.cleanroom_beat_task'. Without it the beat "
        "smoke step fails with NotRegistered at the worker side."
    )


def test_celery_app_wires_env_gated_beat_schedule(beat_task_text: str):
    # The opt-in must be env-gated so production deploys never
    # fire the cleanroom heartbeat. The wiring lives in
    # workers/celery_app.py near the include list.
    celery_app_text = _CELERY_APP_PY.read_text(encoding="utf-8")
    assert _OPT_IN_ENV in celery_app_text, (
        f"workers/celery_app.py must reference the opt-in env "
        f"variable {_OPT_IN_ENV!r}. Without env-gating, production "
        "beat would also fire the cleanroom heartbeat - leaking "
        "test infrastructure into operational deploys."
    )
    assert _CANONICAL_TASK_NAME in celery_app_text, (
        f"workers/celery_app.py must wire the canonical task name "
        f"{_CANONICAL_TASK_NAME!r} into the env-gated beat_schedule "
        "block."
    )
