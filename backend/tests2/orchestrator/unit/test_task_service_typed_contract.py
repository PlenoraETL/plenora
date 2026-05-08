"""Typed task service contract tests."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.orchestrator.task_service import (
    ActiveTaskInfo,
    TaskResultPayload,
    TaskService,
    TaskStatus,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.orchestrator,
    pytest.mark.contract,
    pytest.mark.regression,
]


def test_get_task_status_uses_typed_result_payload_inside_core():
    celery_meta = {
        "success": True,
        "project_id": "project-1",
        "result_id": "result-1",
        "output_file": "out.csv",
    }
    async_result = SimpleNamespace(
        state="SUCCESS",
        info=celery_meta,
        name="pipeline.execute",
    )

    with patch(
        "app.core.orchestrator.task_service.AsyncResult",
        return_value=async_result,
    ):
        task_info = TaskService().get_task_status("task-1")

    assert task_info.status is TaskStatus.SUCCESS
    assert isinstance(task_info.result, TaskResultPayload)
    assert not isinstance(task_info.result, dict)
    assert task_info.project_id == "project-1"
    assert task_info.result.result_id == "result-1"

    compat_payload = task_info.to_dict()
    assert compat_payload["result"] == celery_meta
    assert compat_payload["status"] == "success"


def test_list_active_tasks_returns_typed_task_rows():
    inspect = SimpleNamespace(
        active=lambda: {
            "worker-1": [
                {"id": "task-active-1", "name": "pipeline.execute"},
            ]
        },
        reserved=lambda: {
            "worker-2": [
                {"id": "task-reserved-1", "name": "pipeline.preview"},
            ]
        },
    )
    app = SimpleNamespace(
        control=SimpleNamespace(inspect=lambda: inspect),
    )

    service = TaskService()
    service.app = app

    tasks = service.list_active_tasks()

    assert tasks == [
        ActiveTaskInfo(
            task_id="task-active-1",
            name="pipeline.execute",
            worker="worker-1",
            status="active",
        ),
        ActiveTaskInfo(
            task_id="task-reserved-1",
            name="pipeline.preview",
            worker="worker-2",
            status="reserved",
        ),
    ]
    assert all(isinstance(task, ActiveTaskInfo) for task in tasks)
    assert tasks[0].to_dict() == {
        "task_id": "task-active-1",
        "name": "pipeline.execute",
        "worker": "worker-1",
        "status": "active",
    }


def test_get_task_result_returns_typed_payload():
    celery_result = {
        "success": True,
        "project_id": "project-1",
        "result_id": "result-1",
    }
    async_result = SimpleNamespace(
        state="SUCCESS",
        result=celery_result,
    )

    with patch(
        "app.core.orchestrator.task_service.AsyncResult",
        return_value=async_result,
    ):
        result = TaskService().get_task_result("task-1")

    assert isinstance(result, TaskResultPayload)
    assert result.project_id == "project-1"
    assert result.to_dict() == celery_result
