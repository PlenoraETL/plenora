"""Typed task service contract tests."""

import inspect
from types import SimpleNamespace
import ast
from pathlib import Path
from typing import get_type_hints
from unittest.mock import patch

import pytest

from app.core.orchestrator.task_service import (
    ActiveTaskInfo,
    CeleryLaunchResult,
    PipelineTaskPayload,
    TaskInfo,
    TaskResultPayload,
    TaskService,
    TaskStatus,
    TaskSubmitRequest,
    TaskType,
    task_payload_to_celery_kwargs,
)
from app.core.orchestrator.task_metadata_store import (
    InMemoryTaskMetadataStore,
    PendingTaskMetadata,
)
from app.core.orchestrator.router_mappers import task_info_to_dict, task_result_to_dict


pytestmark = [
    pytest.mark.unit,
    pytest.mark.orchestrator,
    pytest.mark.contract,
    pytest.mark.regression,
]

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


def test_orchestrator_application_code_uses_task_service_boundary():
    for rel_path in (
        "app/core/orchestrator/router.py",
        "app/core/orchestrator/service.py",
    ):
        imports = _imports(rel_path)
        assert "celery.result" not in imports
        assert not any(module.startswith("workers.tasks") for module in imports)


def test_task_service_depends_on_runtime_port_not_celery_directly():
    imports = _imports("app/core/orchestrator/task_service.py")

    assert "celery.result" not in imports
    assert "workers.celery_app" not in imports
    assert not any(module.startswith("workers.tasks") for module in imports)
    assert "app.core.task_runtime" in imports


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
        "app.core.task_runtime.orchestrator.AsyncResult",
        return_value=async_result,
    ):
        task_info = TaskService().get_task_status("task-1")

    assert task_info.status is TaskStatus.SUCCESS
    assert isinstance(task_info.result, TaskResultPayload)
    assert not isinstance(task_info.result, dict)
    assert task_info.project_id == "project-1"
    assert task_info.result.result_id == "result-1"

    compat_payload = task_info_to_dict(task_info)
    assert compat_payload["result"] == celery_meta
    assert compat_payload["status"] == "success"


def test_get_task_status_preserves_managed_failure_result_payload():
    celery_meta = {
        "success": False,
        "project_id": "project-1",
        "message": "Il progetto sorgente e' stato modificato dopo l'ultima esecuzione.",
    }
    async_result = SimpleNamespace(
        state="SUCCESS",
        info=celery_meta,
        name="pipeline.execute",
    )

    with patch(
        "app.core.task_runtime.orchestrator.AsyncResult",
        return_value=async_result,
    ):
        task_info = TaskService().get_task_status("task-1")

    assert task_info.status is TaskStatus.FAILURE
    assert isinstance(task_info.result, TaskResultPayload)
    assert task_info.result.success is False
    assert task_info.error == celery_meta["message"]

    compat_payload = task_info_to_dict(task_info)
    assert compat_payload["status"] == "failure"
    assert compat_payload["result"] == celery_meta


def test_get_task_status_uses_pending_metadata_store_before_celery_metadata():
    metadata_store = InMemoryTaskMetadataStore()
    metadata_store.put(
        PendingTaskMetadata(
            task_id="task-pending-1",
            project_id="project-pending-1",
            task_type="preview",
        )
    )
    async_result = SimpleNamespace(
        state="PENDING",
        info=None,
        name=None,
    )

    with patch(
        "app.core.task_runtime.orchestrator.AsyncResult",
        return_value=async_result,
    ):
        task_info = TaskService(metadata_store=metadata_store).get_task_status(
            "task-pending-1"
        )

    assert task_info.status is TaskStatus.PENDING
    assert task_info.project_id == "project-pending-1"
    assert task_info.task_type is TaskType.PREVIEW
    assert task_info.result is None


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
    assert task_result_to_dict(payload) == celery_meta


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
    assert task_result_to_dict(payload)["error"] == "boom"


def test_task_info_result_contract_is_typed_payload():
    payload = TaskResultPayload.from_celery_meta({"project_id": "project-1"})

    task_info = TaskInfo(
        task_id="task-1",
        task_type=TaskType.EXECUTE,
        project_id="project-1",
        status=TaskStatus.SUCCESS,
        result=payload,
    )

    assert isinstance(task_info.result, TaskResultPayload)
    assert task_info.result.project_id == "project-1"


def test_task_info_result_annotation_does_not_regress_to_broad_dict():
    result_annotation = get_type_hints(TaskInfo)["result"]

    assert "dict" not in str(result_annotation)
    assert "TaskResultPayload" in str(result_annotation)


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

    serialized = task_info_to_dict(task_info)

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
        "app.core.task_runtime.orchestrator.AsyncResult",
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

    metadata_store = InMemoryTaskMetadataStore()
    service = TaskService(metadata_store=metadata_store)
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
        "app.core.task_runtime.orchestrator.AsyncResult",
        return_value=async_result,
    ):
        result = TaskService().get_task_result("task-1")

    assert isinstance(result, TaskResultPayload)
    assert result.project_id == "project-1"
    assert task_result_to_dict(result) == celery_result


def test_forget_task_result_delegates_to_celery_boundary():
    forget_calls = []
    async_result = SimpleNamespace(forget=lambda: forget_calls.append("forgot"))

    with patch(
        "app.core.task_runtime.orchestrator.AsyncResult",
        return_value=async_result,
    ) as async_result_cls:
        service = TaskService()
        service.forget_task_result("task-1")

    async_result_cls.assert_called_once_with("task-1", app=service.app)
    assert forget_calls == ["forgot"]


def test_submit_task_launches_celery_and_returns_pending_task_info():
    captured_kwargs = {}

    class FakeCeleryTask:
        def delay(self, **kwargs):
            captured_kwargs.update(kwargs)
            return CeleryLaunchResult(id="task-submit-1")

    metadata_store = InMemoryTaskMetadataStore()
    service = TaskService(metadata_store=metadata_store)
    task_info = service.submit_task(
        TaskSubmitRequest(
            celery_task=FakeCeleryTask(),
            task_type=TaskType.EXECUTE,
            project_id="project-1",
            payload=PipelineTaskPayload(
                project_id="project-1",
                force_recalculate=False,
                user_id="user-1",
            ),
            queued_message="Pipeline in coda...",
            log_label="execute",
        )
    )

    assert task_info.task_id == "task-submit-1"
    assert task_info.task_type is TaskType.EXECUTE
    assert task_info.project_id == "project-1"
    assert task_info.status is TaskStatus.PENDING
    assert task_info.message == "Pipeline in coda..."
    assert captured_kwargs == {
        "project_id": "project-1",
        "force_recalculate": False,
        "triggered_by": "manual",
        "user_id": "user-1",
        "intermediate_mode_override": None,
    }
    assert metadata_store.get("task-submit-1") == PendingTaskMetadata(
        task_id="task-submit-1",
        project_id="project-1",
        task_type="execute",
    )


def test_task_payload_to_celery_kwargs_rejects_unknown_payload():
    with pytest.raises(TypeError, match="Unsupported task launch payload"):
        task_payload_to_celery_kwargs(object())  # type: ignore[arg-type]


def test_pipeline_payload_includes_execution_contract_only_when_present():
    contract = {
        "project_id": "project-1",
        "project_type": "estrai",
        "group_id": "grp-1",
        "canvas": {"nodes": [], "edges": []},
        "artifact_refs": {},
    }

    kwargs = dict(
        task_payload_to_celery_kwargs(
            PipelineTaskPayload(
                project_id="project-1",
                execution_contract=contract,
            )
        )
    )

    assert kwargs["execution_contract"] == contract


def test_task_service_create_methods_stay_lifecycle_only():
    create_methods = {
        name
        for name, member in inspect.getmembers(TaskService, inspect.isfunction)
        if name.startswith("create_") and name.endswith("_task")
    }

    assert create_methods == {
        "create_carica_task",
        "create_enrichment_task",
        "create_pipeline_task",
        "create_preview_task",
    }


def test_task_service_has_no_magic_getattr_compat_layer():
    assert "__getattr__" not in TaskService.__dict__


def test_task_service_has_no_process_local_pending_project_cache():
    assert "_task_project_cache" not in TaskService.__dict__


def test_preview_router_uses_task_service_boundary(monkeypatch):
    from app.core.orchestrator import router as router_mod
    from app.core.orchestrator.task_service import task_service

    router_mod.init_orchestrator(object())
    monkeypatch.setattr(router_mod, "_assert_project_group", lambda *args, **kwargs: None)

    captured_kwargs = {}

    def fake_create_preview_task(**kwargs):
        captured_kwargs.update(kwargs)
        return TaskInfo(
            task_id="preview-task-1",
            task_type=TaskType.PREVIEW,
            project_id=kwargs["project_id"],
            status=TaskStatus.PENDING,
            message="Preview in coda...",
        )

    def fail_submit_task(_request):
        raise AssertionError("preview router bypassed create_preview_task")

    monkeypatch.setattr(task_service, "create_preview_task", fake_create_preview_task)
    monkeypatch.setattr(task_service, "submit_task", fail_submit_task)

    response = router_mod.preview_project(
        project_id="project-1",
        limit=42,
        current_user=SimpleNamespace(user_id="user-1"),
    )

    assert response["task_id"] == "preview-task-1"
    assert response["task_type"] == "preview"
    assert response["project_id"] == "project-1"
    assert response["async"] is True
    assert captured_kwargs == {
        "project_id": "project-1",
        "limit": 42,
        "user_id": "user-1",
    }


@pytest.mark.parametrize(
    ("method_name", "call_kwargs", "expected_task_type", "expected_message", "expected_kwargs"),
    [
        (
            "create_enrichment_task",
            {
                "project_id": "project-1",
                "canvas": {"nodes": [], "edges": []},
                "output_format": "csv",
                "force_recalculate": True,
                "cleanup_file_source_ids": ["source-1"],
                "is_eseguibile": True,
                "user_id": "user-1",
                "intermediate_mode_override": "save",
            },
            TaskType.ENRICHMENT,
            "Task in coda...",
            {
                "project_id": "project-1",
                "canvas": {"nodes": [], "edges": []},
                "output_format": "csv",
                "force_recalculate": True,
                "cleanup_file_source_ids": ["source-1"],
                "is_eseguibile": True,
                "user_id": "user-1",
                "intermediate_mode_override": "save",
            },
        ),
        (
            "create_carica_task",
            {
                "project_id": "project-1",
                "canvas": {"nodes": [], "edges": []},
                "carica_node_data": {"id": "carica-1", "dest_type": "pg"},
                "output_format": "geojson",
                "force_recalculate": True,
                "triggered_by": "cron",
                "user_id": "user-1",
            },
            TaskType.CARICA,
            "Task CARICA in coda...",
            {
                "project_id": "project-1",
                "canvas": {"nodes": [], "edges": []},
                "carica_node_data": {"id": "carica-1", "dest_type": "pg"},
                "output_format": "geojson",
                "force_recalculate": True,
                "triggered_by": "cron",
                "user_id": "user-1",
            },
        ),
        (
            "create_pipeline_task",
            {
                "project_id": "project-1",
                "force_recalculate": True,
                "triggered_by": "manual",
                "user_id": "user-1",
                "intermediate_mode_override": "save",
            },
            TaskType.EXECUTE,
            "Pipeline in coda...",
            {
                "project_id": "project-1",
                "force_recalculate": True,
                "triggered_by": "manual",
                "user_id": "user-1",
                "intermediate_mode_override": "save",
            },
        ),
        (
            "create_preview_task",
            {"project_id": "project-1", "limit": 25, "user_id": "user-1"},
            TaskType.PREVIEW,
            "Preview in coda...",
            {"project_id": "project-1", "limit": 25, "user_id": "user-1"},
        ),
    ],
)
def test_create_task_wrappers_delegate_to_generic_submit(
    monkeypatch,
    method_name,
    call_kwargs,
    expected_task_type,
    expected_message,
    expected_kwargs,
):
    service = TaskService()
    captured_requests = []

    def fake_submit_task(request):
        captured_requests.append(request)
        return TaskInfo(
            task_id="task-1",
            task_type=request.task_type,
            project_id=request.project_id,
            status=TaskStatus.PENDING,
            message=request.queued_message,
        )

    monkeypatch.setattr(service, "submit_task", fake_submit_task)

    task_info = getattr(service, method_name)(**call_kwargs)

    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert request.task_type is expected_task_type
    assert request.project_id == "project-1"
    assert dict(task_payload_to_celery_kwargs(request.payload)) == expected_kwargs
    assert request.queued_message == expected_message
    assert hasattr(request.celery_task, "delay")
    assert task_info.task_type is expected_task_type
    assert task_info.status is TaskStatus.PENDING


def test_create_task_router_uses_enrichment_task_service_boundary(monkeypatch):
    from app.core.orchestrator import router as router_mod
    from app.core.orchestrator.task_service import task_service

    captured_kwargs = {}

    def fake_create_enrichment_task(**kwargs):
        captured_kwargs.update(kwargs)
        return TaskInfo(
            task_id="task-1",
            task_type=TaskType.ENRICHMENT,
            project_id=kwargs["project_id"],
            status=TaskStatus.PENDING,
            message="Task in coda...",
        )

    def fail_submit_task(_request):
        raise AssertionError("router bypassed create_enrichment_task")

    monkeypatch.setattr("app.core.auth.resource_access.assert_project_access", lambda *a, **kw: None)
    monkeypatch.setattr("app.core.auth.resource_access.assert_canvas_resources_access", lambda *a, **kw: None)
    monkeypatch.setattr(task_service, "create_enrichment_task", fake_create_enrichment_task)
    monkeypatch.setattr(task_service, "submit_task", fail_submit_task)

    response = router_mod.create_task(
        router_mod.CreateTaskRequest(
            task_type=TaskType.ENRICHMENT,
            project_id="project-1",
            canvas={"nodes": [], "edges": []},
            output_format="csv",
            force_recalculate=True,
        ),
        current_user=SimpleNamespace(user_id="user-1"),
    )

    assert response.task_id == "task-1"
    assert captured_kwargs == {
        "project_id": "project-1",
        "canvas": {"nodes": [], "edges": []},
        "output_format": "csv",
        "force_recalculate": True,
        "user_id": "user-1",
    }


def test_create_task_router_uses_carica_task_service_boundary(monkeypatch):
    from app.core.orchestrator import router as router_mod
    from app.core.orchestrator.task_service import task_service

    captured_kwargs = {}

    def fake_create_carica_task(**kwargs):
        captured_kwargs.update(kwargs)
        return TaskInfo(
            task_id="task-1",
            task_type=TaskType.CARICA,
            project_id=kwargs["project_id"],
            status=TaskStatus.PENDING,
            message="Task CARICA in coda...",
        )

    def fail_submit_task(_request):
        raise AssertionError("router bypassed create_carica_task")

    monkeypatch.setattr("app.core.auth.resource_access.assert_project_access", lambda *a, **kw: None)
    monkeypatch.setattr("app.core.auth.resource_access.assert_canvas_resources_access", lambda *a, **kw: None)
    monkeypatch.setattr(task_service, "create_carica_task", fake_create_carica_task)
    monkeypatch.setattr(task_service, "submit_task", fail_submit_task)

    response = router_mod.create_task(
        router_mod.CreateTaskRequest(
            task_type=TaskType.CARICA,
            project_id="project-1",
            canvas={"nodes": [], "edges": []},
            carica_node_data={"id": "carica-1", "dest_type": "pg"},
            output_format="geojson",
            force_recalculate=True,
            triggered_by="cron",
        ),
        current_user=SimpleNamespace(user_id="user-1"),
    )

    assert response.task_id == "task-1"
    assert captured_kwargs == {
        "project_id": "project-1",
        "canvas": {"nodes": [], "edges": []},
        "carica_node_data": {"id": "carica-1", "dest_type": "pg"},
        "output_format": "geojson",
        "force_recalculate": True,
        "triggered_by": "cron",
        "user_id": "user-1",
    }
