"""Typed task service contract tests."""

import inspect
from types import SimpleNamespace
import ast
from pathlib import Path
from typing import get_type_hints

import pytest

from app.core.orchestrator.task_service import (
    CeleryLaunchResult,
    EnrichmentTaskPayload,
    PipelineTaskPayload,
    TaskInfo,
    TaskResultPayload,
    TaskService,
    TaskStatus,
    TaskSubmitRequest,
    TaskType,
    task_payload_to_celery_kwargs,
    _pending_metadata_for_submit,
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


def test_executable_enrichment_metadata_carries_tenant_and_owner():
    request = TaskSubmitRequest(
        celery_task=SimpleNamespace(name="tasks.execute_enrichment"),
        task_type=TaskType.ENRICHMENT,
        project_id="exec_1",
        payload=EnrichmentTaskPayload(
            project_id="exec_1",
            canvas={},
            is_eseguibile=True,
            user_id="u-1",
            execution_context={"eseguibile_id": "exec_1", "group_id": "g-1"},
        ),
        queued_message="queued",
        log_label="enrichment",
    )

    metadata = _pending_metadata_for_submit(request, task_id="task-1")

    assert metadata.subject_kind == "executable"
    assert metadata.subject_id == "exec_1"
    assert metadata.group_id == "g-1"
    assert metadata.owner_user_id == "u-1"


def test_executable_pipeline_metadata_uses_execution_subject():
    request = TaskSubmitRequest(
        celery_task=SimpleNamespace(name="pipeline.execute"),
        task_type=TaskType.EXECUTE,
        project_id="published-model-1",
        payload=PipelineTaskPayload(
            project_id="published-model-1",
            user_id="u-2",
            execution_contract={
                "group_id": "g-2",
                "execution_subject": {
                    "kind": "eseguibile",
                    "id": "published-model-1",
                },
            },
        ),
        queued_message="queued",
        log_label="execute",
    )

    metadata = _pending_metadata_for_submit(request, task_id="task-2")

    assert metadata.subject_kind == "executable"
    assert metadata.subject_id == "published-model-1"
    assert metadata.group_id == "g-2"
    assert metadata.owner_user_id == "u-2"

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


class _StaticRuntime:
    """Runtime di test: ``get_result`` ritorna un risultato fisso.

    Dopo il flip F2.3 la sorgente di stato/esito e' PostgreSQL
    (task_result_store) e non piu' ``celery.result.AsyncResult``. Questi
    contract verificano il MAPPING (invariato), quindi iniettano il
    risultato direttamente nel runtime invece di patchare il backend."""

    def __init__(self, result: object) -> None:
        self._result = result

    def get_result(self, task_id: str) -> object:
        return self._result


class _ByIdRuntime:
    def __init__(self, states):
        self._states = states

    def get_result(self, task_id: str) -> object:
        return SimpleNamespace(
            state=self._states.get(task_id, "PENDING"),
            info=None,
            name=None,
        )


def test_list_active_tasks_for_project_excludes_terminal_results():
    metadata_store = InMemoryTaskMetadataStore()
    metadata_store.put(PendingTaskMetadata("t-pending", "p1", "execute"))
    metadata_store.put(PendingTaskMetadata("t-running", "p1", "preview"))
    metadata_store.put(PendingTaskMetadata("t-done", "p1", "carica"))
    metadata_store.put(PendingTaskMetadata("t-other", "p2", "execute"))
    service = TaskService(
        runtime=_ByIdRuntime({
            "t-pending": "PENDING",
            "t-running": "PROGRESS",
            "t-done": "SUCCESS",
        }),
        metadata_store=metadata_store,
    )

    assert service.list_active_task_ids_for_project("p1") == [
        "t-pending",
        "t-running",
    ]


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
    stored_result = SimpleNamespace(
        state="SUCCESS",
        info=celery_meta,
        name="pipeline.execute",
    )

    task_info = TaskService(runtime=_StaticRuntime(stored_result)).get_task_status(
        "task-1"
    )

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
    stored_result = SimpleNamespace(
        state="SUCCESS",
        info=celery_meta,
        name="pipeline.execute",
    )

    task_info = TaskService(runtime=_StaticRuntime(stored_result)).get_task_status(
        "task-1"
    )

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
    stored_result = SimpleNamespace(
        state="PENDING",
        info=None,
        name=None,
    )

    task_info = TaskService(
        runtime=_StaticRuntime(stored_result), metadata_store=metadata_store
    ).get_task_status("task-pending-1")

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


def test_task_info_mapper_has_no_to_dict_fallback():
    """Task status serialization is typed-only at the router boundary."""
    from app.core.orchestrator import router_mappers

    src = inspect.getsource(router_mappers.task_info_to_dict)
    body_src = "\n".join(src.splitlines()[1:])
    assert ".to_dict" not in body_src


def test_task_service_does_not_call_task_result_to_dict_internally(monkeypatch):
    def fail_to_dict(_payload: TaskResultPayload) -> dict[str, object]:
        raise AssertionError("TaskService called TaskResultPayload.to_dict()")

    monkeypatch.setattr(TaskResultPayload, "to_dict", fail_to_dict, raising=False)
    celery_meta = {
        "success": True,
        "project_id": "project-1",
        "result_id": "result-1",
    }
    stored_result = SimpleNamespace(
        state="SUCCESS",
        info=celery_meta,
        name="pipeline.execute",
    )

    task_info = TaskService(runtime=_StaticRuntime(stored_result)).get_task_status(
        "task-1"
    )

    assert isinstance(task_info.result, TaskResultPayload)
    assert task_info.result.result_id == "result-1"


def test_task_subject_fallback_uses_result_contract_not_id_prefix():
    metadata_store = InMemoryTaskMetadataStore()
    project_result = SimpleNamespace(
        state="SUCCESS",
        info={
            "success": True,
            "project_id": "exec_project_but_not_executable",
        },
        name="pipeline.execute",
    )
    executable_result = SimpleNamespace(
        state="SUCCESS",
        info={
            "success": True,
            "project_id": "published-model-1",
            "is_eseguibile": True,
        },
        name="pipeline.execute",
    )

    project_info = TaskService(
        runtime=_StaticRuntime(project_result),
        metadata_store=metadata_store,
    ).get_task_status("task-project")
    executable_info = TaskService(
        runtime=_StaticRuntime(executable_result),
        metadata_store=metadata_store,
    ).get_task_status("task-executable")

    assert project_info.subject_kind == "project"
    assert executable_info.subject_kind == "executable"


# Plenora 2.0 Fase 5 (F5.6): list_active_tasks/ActiveTaskInfo rimossi (dead
# code: nessun endpoint li usava; l'inspect dei worker Celery non esiste piu').


def test_get_task_result_returns_typed_payload():
    celery_result = {
        "success": True,
        "project_id": "project-1",
        "result_id": "result-1",
    }
    stored_result = SimpleNamespace(
        state="SUCCESS",
        result=celery_result,
    )

    result = TaskService(runtime=_StaticRuntime(stored_result)).get_task_result(
        "task-1"
    )

    assert isinstance(result, TaskResultPayload)
    assert result.project_id == "project-1"
    assert task_result_to_dict(result) == celery_result


def test_forget_task_result_delegates_to_runtime_port():
    # Dopo il flip F2.3 il forget va allo store PG via runtime.forget_result
    # (la cancellazione effettiva e' coperta in test_pg_task_runtime_contract).
    # Qui si blinda solo che task_service deleghi al port.
    forgotten: list[str] = []

    class _Runtime:
        def forget_result(self, task_id: str) -> None:
            forgotten.append(task_id)

    TaskService(runtime=_Runtime()).forget_task_result("task-1")

    assert forgotten == ["task-1"]


def test_submit_task_launches_celery_and_returns_pending_task_info(monkeypatch):
    captured_kwargs = {}
    captured_name = {}
    events = []

    # Fase 1 cutover: submit_task dispatcha by-name via dispatch_task_by_name
    # (non piu' celery_task.delay). Si patcha il dispatch nel modulo
    # task_service e si verifica nome + kwargs propagati.
    from app.core.orchestrator import task_service as ts_mod

    class OrderingMetadataStore(InMemoryTaskMetadataStore):
        def put(self, metadata):
            events.append("metadata")
            super().put(metadata)

    metadata_store = OrderingMetadataStore()

    def fake_dispatch(name, **options):
        events.append("publish")
        captured_name["name"] = name
        captured_kwargs.update(options.get("kwargs", {}))
        assert metadata_store.get(options["task_id"]) is not None
        return CeleryLaunchResult(id=options["task_id"])

    monkeypatch.setattr(ts_mod, "dispatch_task_by_name", fake_dispatch)

    class FakeCeleryTask:
        name = "pipeline.execute"

    service = TaskService(
        metadata_store=metadata_store,
        task_id_factory=lambda: "task-submit-1",
    )
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

    assert captured_name["name"] == "pipeline.execute"
    assert events == ["metadata", "publish"]
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
        subject_id="project-1",
        owner_user_id="user-1",
    )


def test_submit_task_does_not_publish_when_metadata_persistence_fails(monkeypatch):
    from app.core.orchestrator import task_service as ts_mod

    class BrokenMetadataStore(InMemoryTaskMetadataStore):
        def put(self, metadata):
            raise RuntimeError("metadata unavailable")

    def fail_dispatch(*args, **kwargs):
        raise AssertionError("dispatch must not run")

    monkeypatch.setattr(ts_mod, "dispatch_task_by_name", fail_dispatch)
    service = TaskService(
        metadata_store=BrokenMetadataStore(),
        task_id_factory=lambda: "task-submit-2",
    )

    class FakeCeleryTask:
        name = "pipeline.execute"

    with pytest.raises(RuntimeError, match="metadata unavailable"):
        service.submit_task(
            TaskSubmitRequest(
                celery_task=FakeCeleryTask(),
                task_type=TaskType.EXECUTE,
                project_id="project-1",
                payload=PipelineTaskPayload(project_id="project-1"),
                queued_message="Pipeline in coda...",
                log_label="execute",
            )
        )


def test_submit_task_removes_pending_metadata_when_publish_fails(monkeypatch):
    from app.core.orchestrator import task_service as ts_mod

    def fail_dispatch(*args, **kwargs):
        raise RuntimeError("rabbit unavailable")

    monkeypatch.setattr(ts_mod, "dispatch_task_by_name", fail_dispatch)
    metadata_store = InMemoryTaskMetadataStore()
    service = TaskService(
        metadata_store=metadata_store,
        task_id_factory=lambda: "task-submit-3",
    )

    class FakeCeleryTask:
        name = "pipeline.execute"

    with pytest.raises(RuntimeError, match="rabbit unavailable"):
        service.submit_task(
            TaskSubmitRequest(
                celery_task=FakeCeleryTask(),
                task_type=TaskType.EXECUTE,
                project_id="project-1",
                payload=PipelineTaskPayload(project_id="project-1"),
                queued_message="Pipeline in coda...",
                log_label="execute",
            )
        )

    assert metadata_store.get("task-submit-3") is None


def test_submit_task_outbox_keeps_pending_when_broker_is_temporarily_down(
    monkeypatch,
):
    from app.core.orchestrator import task_service as ts_mod

    class FakeOutbox:
        def __init__(self):
            self.prepared = []
            self.retried = []
            self.published = []

        def prepare(self, metadata, *, actor_name, options):
            self.prepared.append((metadata, actor_name, options))

        def mark_retry(self, task_id, error):
            self.retried.append((task_id, type(error).__name__))

        def mark_published(self, task_id):
            self.published.append(task_id)

    def fail_dispatch(*args, **kwargs):
        raise RuntimeError("rabbit unavailable")

    monkeypatch.setattr(ts_mod, "dispatch_task_by_name", fail_dispatch)
    outbox = FakeOutbox()
    service = TaskService(
        metadata_store=InMemoryTaskMetadataStore(),
        dispatch_outbox=outbox,
        task_id_factory=lambda: "task-outbox-1",
    )

    class FakeCeleryTask:
        name = "pipeline.execute"

    info = service.submit_task(
        TaskSubmitRequest(
            celery_task=FakeCeleryTask(),
            task_type=TaskType.EXECUTE,
            project_id="project-1",
            payload=PipelineTaskPayload(project_id="project-1"),
            queued_message="Pipeline in coda...",
            log_label="execute",
        )
    )

    assert info.task_id == "task-outbox-1"
    assert len(outbox.prepared) == 1
    assert outbox.retried == [("task-outbox-1", "RuntimeError")]
    assert outbox.published == []


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
    monkeypatch.setattr(
        router_mod,
        "_project_service",
        lambda: SimpleNamespace(
            get_project_metadata=lambda _project_id: SimpleNamespace(
                project_type="manipola",
                group_id="group-1",
                retention_n=3,
                default_connection_id=None,
            ),
            get_project_canvas=lambda _project_id: {
                "nodes": [],
                "edges": [],
            },
            get_project_output_contract=lambda _project_id: None,
        ),
    )

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
    assert captured_kwargs["project_id"] == "project-1"
    assert captured_kwargs["limit"] == 42
    assert captured_kwargs["user_id"] == "user-1"
    assert captured_kwargs["execution_contract"]["group_id"] == "group-1"
    assert captured_kwargs["execution_contract"]["canvas"] == {
        "nodes": [],
        "edges": [],
    }


def test_preview_router_preserves_project_not_found(monkeypatch):
    from fastapi import HTTPException
    from app.core.orchestrator import router as router_mod

    router_mod.init_orchestrator(object())
    monkeypatch.setattr(router_mod, "_assert_project_group", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        router_mod,
        "_project_service",
        lambda: SimpleNamespace(
            get_project_metadata=lambda _project_id: None,
            get_project_canvas=lambda _project_id: None,
            get_project_output_contract=lambda _project_id: None,
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        router_mod.preview_project(
            project_id="missing-project",
            limit=10,
            current_user=SimpleNamespace(user_id="user-1"),
        )

    assert exc_info.value.status_code == 404


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
    # Plenora 2.0: dispatch by-name -> il task espone `name`, non piu' `.delay`.
    assert hasattr(request.celery_task, "name")
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

    monkeypatch.setattr(
        "app.core.auth.resource_access.assert_project_access",
        lambda *a, **kw: SimpleNamespace(
            project_type="manipola",
            group_id="group-1",
            canvas_validated=True,
        ),
    )
    monkeypatch.setattr("app.core.auth.resource_access.assert_canvas_resources_access", lambda *a, **kw: None)
    monkeypatch.setattr(
        router_mod,
        "enrich_canvas_types_from_output_contract",
        lambda *_args, **_kwargs: None,
    )
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
        "execution_contract": {
            "schema_version": 1,
            "project_id": "project-1",
            "project_type": "manipola",
            "group_id": "group-1",
            "canvas": {"nodes": [], "edges": []},
            "carica_node_data": {"id": "carica-1", "dest_type": "pg"},
            "output_format": "geojson",
            "force_recalculate": True,
            "triggered_by": "cron",
        },
    }
