"""Contract tests for the scheduler job dispatch boundary."""

from __future__ import annotations

import ast
from datetime import datetime
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


def _imports(rel_path: str) -> set[str]:
    tree = ast.parse((_BACKEND_ROOT / rel_path).read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


class FakeScheduleTask:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def apply_async(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id="scheduled-task-1")


class FakeScheduleRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def send_task(self, name, **kwargs):
        self.calls.append({"name": name, **kwargs})
        return SimpleNamespace(id="scheduled-runtime-1")


def _assert_auto_scheduled_for(kwargs: dict[str, object]) -> None:
    scheduled_for = kwargs.get("scheduled_for")
    assert isinstance(scheduled_for, str)
    datetime.fromisoformat(scheduled_for)


def test_enqueue_schedule_run_adapts_manual_run_to_runtime_shape() -> None:
    task = FakeScheduleTask()

    handle = enqueue_schedule_run(
        "schedule-1",
        triggered_by="manual",
        retry_attempt=0,
        task=task,
    )

    assert handle.id == "scheduled-task-1"
    assert task.calls[0]["args"] == []
    kwargs = task.calls[0]["kwargs"]
    assert kwargs["schedule_id"] == "schedule-1"
    assert kwargs["triggered_by"] == "manual"
    assert kwargs["retry_attempt"] == 0
    _assert_auto_scheduled_for(kwargs)


def test_enqueue_schedule_run_dispatches_by_task_name_through_runtime() -> None:
    runtime = FakeScheduleRuntime()

    handle = enqueue_schedule_run(
        "schedule-1",
        triggered_by="manual",
        retry_attempt=0,
        runtime=runtime,
    )

    assert handle.id == "scheduled-runtime-1"
    assert runtime.calls[0]["name"] == SCHEDULE_PIPELINE_TASK_NAME
    assert runtime.calls[0]["args"] == []
    kwargs = runtime.calls[0]["kwargs"]
    assert kwargs["schedule_id"] == "schedule-1"
    assert kwargs["triggered_by"] == "manual"
    assert kwargs["retry_attempt"] == 0
    _assert_auto_scheduled_for(kwargs)


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
            "args": [],
            "kwargs": {
                "schedule_id": "schedule-2",
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
    cron_kwargs = scheduled_run_kwargs(triggered_by="cron")
    assert cron_kwargs["triggered_by"] == "cron"
    assert cron_kwargs["retry_attempt"] == 0
    _assert_auto_scheduled_for(cron_kwargs)
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
        imports = _imports(path.relative_to(_BACKEND_ROOT).as_posix())

        assert "workers.tasks.schedule_task" not in imports, path


def test_scheduler_dispatcher_does_not_import_concrete_task_module() -> None:
    dispatcher_imports = _imports("app/core/scheduler/job_dispatcher.py")

    assert "workers.tasks.schedule_task" not in dispatcher_imports
    assert "workers.celery_app" not in dispatcher_imports


def test_scheduler_dispatcher_uses_shared_task_runtime_boundary() -> None:
    dispatcher_imports = _imports("app/core/scheduler/job_dispatcher.py")

    assert "app.core.task_runtime.dispatch" in dispatcher_imports


def test_beat_scheduler_uses_dispatcher_task_name_constant() -> None:
    beat_source = (_BACKEND_ROOT / "workers" / "beat_scheduler.py").read_text(
        encoding="utf-8"
    )

    assert "SCHEDULE_PIPELINE_TASK_NAME" in beat_source
    assert "workers.tasks.schedule_task.schedule_pipeline_task" not in beat_source
