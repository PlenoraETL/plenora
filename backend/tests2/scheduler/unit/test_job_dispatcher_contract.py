"""Contract tests for the scheduler job dispatch boundary."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.scheduler.job_dispatcher import (
    SCHEDULE_PIPELINE_TASK_NAME,
    enqueue_schedule_run,
    scheduled_run_kwargs,
)


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


class FakeScheduleTask:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def apply_async(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id="scheduled-task-1")


def test_enqueue_schedule_run_adapts_manual_run_to_runtime_shape() -> None:
    task = FakeScheduleTask()

    handle = enqueue_schedule_run(
        "schedule-1",
        triggered_by="manual",
        retry_attempt=0,
        task=task,
    )

    assert handle.id == "scheduled-task-1"
    assert task.calls == [
        {
            "args": ["schedule-1"],
            "kwargs": {"triggered_by": "manual", "retry_attempt": 0},
        }
    ]


def test_enqueue_schedule_run_adapts_retry_countdown_and_scheduled_for() -> None:
    task = FakeScheduleTask()

    enqueue_schedule_run(
        "schedule-2",
        triggered_by="cron",
        retry_attempt=2,
        scheduled_for="2026-05-14T10:00:00+00:00",
        countdown=30,
        task=task,
    )

    assert task.calls == [
        {
            "args": ["schedule-2"],
            "kwargs": {
                "triggered_by": "cron",
                "retry_attempt": 2,
                "scheduled_for": "2026-05-14T10:00:00+00:00",
            },
            "countdown": 30,
        }
    ]


def test_scheduler_task_name_and_kwargs_are_canonical() -> None:
    assert SCHEDULE_PIPELINE_TASK_NAME == (
        "workers.tasks.schedule_task.schedule_pipeline_task"
    )
    assert scheduled_run_kwargs(triggered_by="cron") == {
        "triggered_by": "cron",
        "retry_attempt": 0,
    }
    assert scheduled_run_kwargs(
        triggered_by="catchup",
        scheduled_for="2026-05-14T10:00:00+00:00",
    ) == {
        "triggered_by": "catchup",
        "retry_attempt": 0,
        "scheduled_for": "2026-05-14T10:00:00+00:00",
    }


def test_scheduler_application_code_uses_dispatch_boundary() -> None:
    scheduler_root = _BACKEND_ROOT / "app" / "core" / "scheduler"
    checked_paths = [
        path
        for path in scheduler_root.rglob("*.py")
        if path.name != "job_dispatcher.py"
    ]
    for path in checked_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)

        assert "workers.tasks.schedule_task" not in imports, path


def test_beat_scheduler_uses_dispatcher_task_name_constant() -> None:
    beat_source = (_BACKEND_ROOT / "workers" / "beat_scheduler.py").read_text(
        encoding="utf-8"
    )

    assert "SCHEDULE_PIPELINE_TASK_NAME" in beat_source
    assert "workers.tasks.schedule_task.schedule_pipeline_task" not in beat_source
