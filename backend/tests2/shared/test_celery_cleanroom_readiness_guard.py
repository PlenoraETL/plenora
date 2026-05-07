"""
Architectural lock for the Celery cleanroom readiness step.

BE-GO-READY-INSTALLATION-014-CELERY-CLEANROOM-READINESS.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci-cleanroom.yml"
_CELERY_APP_PY = (
    _REPO_ROOT / "backend" / "workers" / "celery_app.py"
)
_BEAT_SCHEDULER_PY = (
    _REPO_ROOT / "backend" / "workers" / "beat_scheduler.py"
)


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return _WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def celery_app_text() -> str:
    return _CELERY_APP_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def beat_scheduler_text() -> str:
    return _BEAT_SCHEDULER_PY.read_text(encoding="utf-8")


# 1. Workflow contains the Celery readiness step


def test_ci_cleanroom_workflow_has_celery_readiness_step(workflow_text: str):
    assert "Verify Celery cleanroom readiness" in workflow_text, (
        "ci-cleanroom.yml is missing the 'Verify Celery cleanroom "
        "readiness' step. The Celery surface (worker import + broker "
        "connectivity + beat scheduler symbol) must be probed in CI; "
        "without it, an import-time bug in any task module would only "
        "surface in production at first worker startup."
    )


def test_ci_cleanroom_workflow_step_probes_canonical_surface(
    workflow_text: str,
):
    # We assert the probe touches each of the four canonical surfaces
    # rather than pin the exact Python source: the runtime check can
    # be reformatted without breaking this lock as long as it still
    # asserts the contracts.
    expectations = (
        # 1. Celery app main name
        ('celery_app.main == "dataflow_tasks"',
         "Probe must assert the Celery main name is 'dataflow_tasks' "
         "(systemd / CLI contract)."),
        # 2. Include modules import
        ('workers.tasks.enrichment_task',
         "Probe must reference workers.tasks.enrichment_task in the "
         "include-module import-loop."),
        ('workers.tasks.carica_task',
         "Probe must reference workers.tasks.carica_task."),
        ('workers.tasks.schedule_task',
         "Probe must reference workers.tasks.schedule_task."),
        ('workers.tasks.pipeline_task',
         "Probe must reference workers.tasks.pipeline_task."),
        ('workers.tasks.imdf_publish_task',
         "Probe must reference workers.tasks.imdf_publish_task."),
        # 3. Broker connectivity
        ('ensure_connection',
         "Probe must call ensure_connection() against the broker."),
        # 4. Beat scheduler symbol
        ('PlenoraBeatScheduler',
         "Probe must resolve PlenoraBeatScheduler symbol "
         "(plenora-beat.service ExecStart contract)."),
    )
    for needle, message in expectations:
        assert needle in workflow_text, message


def test_ci_cleanroom_celery_probe_is_bounded_and_sleep_free(
    workflow_text: str,
):
    # Slice the Celery readiness step out of the workflow to scope
    # the assertion to that step only. We do NOT pin the slice end
    # to "Stop backend" because INSTALLATION-019 inserted the
    # enqueue smoke step between readiness and Stop backend; the
    # slice must end at the next `- name:` step header.
    lines = workflow_text.splitlines()
    start_idx = next(
        i for i, line in enumerate(lines)
        if "Verify Celery cleanroom readiness" in line
    )
    end_idx = next(
        (
            i
            for i, line in enumerate(
                lines[start_idx + 1 :], start=start_idx + 1
            )
            if line.lstrip().startswith("- name:")
        ),
        len(lines),
    )
    step_block = "\n".join(lines[start_idx:end_idx])

    # No fragile sleep loops in the readiness step. We strip YAML
    # comment lines first so the rationale prose can mention
    # "sleep loops" without false-positives. The check then targets
    # actual shell `sleep` invocations.
    code_only_lines = [
        line for line in step_block.splitlines()
        if not line.lstrip().startswith("#")
    ]
    code_only = "\n".join(code_only_lines)
    assert " sleep " not in code_only and "\nsleep " not in code_only, (
        "Celery readiness step must not contain 'sleep' shell "
        "invocations - the readiness contract "
        "(BE-GO-READY-INSTALLATION-014) is explicitly sleep-free; "
        "use bounded timeouts instead."
    )
    assert "timeout=5" in step_block, (
        "Celery broker probe must use timeout=5 (bounded, single "
        "attempt) - the contract is sleep-free with a hard cap."
    )
    assert "max_retries=1" in step_block, (
        "Celery broker probe must use max_retries=1 - retry loops "
        "introduce non-determinism into CI."
    )


# 2. workers.celery_app contract preserved


def test_celery_app_module_pins_canonical_main_name(celery_app_text: str):
    # The systemd unit and CLI both depend on `celery -A
    # workers.celery_app` resolving to a Celery instance whose
    # `main` is "dataflow_tasks". A rename here silently breaks the
    # deploy.
    assert 'Celery(\n    "dataflow_tasks",' in celery_app_text or \
           'Celery("dataflow_tasks",' in celery_app_text, (
        "workers/celery_app.py must construct Celery(...) with the "
        "canonical main name 'dataflow_tasks' (systemd "
        "plenora-celery.service ExecStart depends on it)."
    )


def test_celery_app_includes_canonical_task_modules(celery_app_text: str):
    # The five include modules are the runtime contract that the
    # cleanroom probe asserts. Removing one without coordinating the
    # probe would let the probe pass while production silently drops
    # a task family.
    for module_path in (
        "workers.tasks.enrichment_task",
        "workers.tasks.carica_task",
        "workers.tasks.schedule_task",
        "workers.tasks.pipeline_task",
        "workers.tasks.imdf_publish_task",
    ):
        assert f'"{module_path}"' in celery_app_text, (
            f"workers/celery_app.py include=[...] is missing "
            f"{module_path!r}. If this module was intentionally "
            "removed, update the cleanroom probe in "
            ".github/workflows/ci-cleanroom.yml in the same change."
        )


# 3. PlenoraBeatScheduler symbol preserved


def test_beat_scheduler_class_name_preserved(beat_scheduler_text: str):
    assert "class PlenoraBeatScheduler" in beat_scheduler_text, (
        "workers/beat_scheduler.py must export "
        "PlenoraBeatScheduler. The systemd plenora-beat.service "
        "ExecStart `-S workers.beat_scheduler.PlenoraBeatScheduler` "
        "resolves to this class; renaming breaks the deploy "
        "silently."
    )
