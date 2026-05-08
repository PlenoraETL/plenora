"""Typed task service contract tests."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.orchestrator.task_service import (
    ActiveTaskInfo,
    TaskInfo,
    TaskResultPayload,
    TaskService,
    TaskStatus,
    TaskType,
)
from app.core.orchestrator.router import _task_info_to_dict, _task_result_to_dict


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

    compat_payload = _task_info_to_dict(task_info)
    assert compat_payload["result"] == celery_meta
    assert compat_payload["status"] == "success"


def test_task_result_payload_normalizes_success_metadata_without_raw_mapping():
    celery_meta = {
        "success": True,
        "project_id": 123,
        "result_id": "result-1",
        "output_file": "out.csv",
        "rows_count": "42",
        "preview": [{"id": 1}],
    }

    payload = TaskResultPayload.from_celery_meta(celery_meta)

    assert payload.success is True
    assert payload.project_id == "123"
    assert payload.result_id == "result-1"
    assert payload.output_file == "out.csv"
    assert payload.rows_count == 42
    assert not hasattr(payload, "payload")
    assert _task_result_to_dict(payload) == celery_meta


def test_task_result_payload_keeps_malformed_fields_non_fatal_and_typed():
    celery_meta = {
        "success": "yes",
        "project_id": None,
        "result_id": None,
        "rows_count": "not-an-int",
        "error": RuntimeError("boom"),
    }

    payload = TaskResultPayload.from_celery_meta(celery_meta)

    assert payload.success is None
    assert payload.project_id == ""
    assert payload.result_id is None
    assert payload.rows_count is None
    assert payload.error == "boom"
    assert _task_result_to_dict(payload)["error"] == "boom"


def test_task_info_does_not_normalize_result_in_post_init():
    task_info = TaskInfo(
        task_id="task-1",
        task_type=TaskType.EXECUTE,
        project_id="project-1",
        status=TaskStatus.SUCCESS,
        result={"project_id": "project-1"},
    )

    assert isinstance(task_info.result, dict)


def test_router_serializes_typed_task_result_payload_at_edge():
    payload = TaskResultPayload.from_celery_meta(
        {
            "success": True,
            "project_id": "project-1",
            "result_id": "result-1",
        }
    )
    task_info = TaskInfo(
        task_id="task-1",
        task_type=TaskType.EXECUTE,
        project_id="project-1",
        status=TaskStatus.SUCCESS,
        result=payload,
    )

    serialized = _task_info_to_dict(task_info)

    assert serialized["result"] == {
        "success": True,
        "project_id": "project-1",
        "result_id": "result-1",
    }


def test_task_service_does_not_call_task_result_to_dict_internally(monkeypatch):
    def fail_to_dict(_payload: TaskResultPayload) -> dict[str, object]:
        raise AssertionError("TaskService called TaskResultPayload.to_dict()")

    monkeypatch.setattr(TaskResultPayload, "to_dict", fail_to_dict, raising=False)
    celery_meta = {
        "success": True,
        "project_id": "project-1",
        "result_id": "result-1",
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

    assert isinstance(task_info.result, TaskResultPayload)
    assert task_info.result.result_id == "result-1"


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
    assert _task_result_to_dict(result) == celery_result
