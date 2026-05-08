"""Typed task service contract tests."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.orchestrator.task_service import (
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
