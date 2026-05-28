"""Contract tests for OrchestratorService executor dependency injection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.shared.execution.executor_protocol import ExecutionResult


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_RUNTIME_SERVICES = _BACKEND_ROOT / "app" / "runtime" / "services.py"
_PIPELINE_TASK = _BACKEND_ROOT / "workers" / "tasks" / "pipeline_task.py"
_CARICA_TASK = _BACKEND_ROOT / "workers" / "tasks" / "carica_task.py"
_ORCHESTRATOR_SERVICE = _BACKEND_ROOT / "app" / "core" / "orchestrator" / "service.py"


class _FakeExecutor:
    def __init__(self, result: ExecutionResult | None = None) -> None:
        self.execute_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.preview_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.result = result or ExecutionResult(
            success=True,
            payload={"result_id": "fake-result", "rows_count": 3},
        )

    def execute(self, *args: object, **kwargs: object) -> ExecutionResult:
        self.execute_calls.append((args, kwargs))
        return self.result

    def preview(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.preview_calls.append((args, kwargs))
        return {"success": True, "rows": [{"id": 1}]}


def test_orchestrator_accepts_injected_executors_without_default_construction() -> None:
    """Injected executor protocols are enough to run, no concrete builders needed."""
    from app.core.artifacts.types import PipelineArtifact
    from app.core.orchestrator.service import OrchestratorService

    project_service = MagicMock()
    canvas = MagicMock()
    project_service.get_project_metadata.return_value = MagicMock(project_type="estrai")
    project_service.get_project_canvas.return_value = canvas

    estrai_executor = _FakeExecutor()
    svc = OrchestratorService(
        project_service=project_service,
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
        estrai_executor=estrai_executor,
        manipola_executor=_FakeExecutor(),
        enrich_executor=_FakeExecutor(),
    )
    svc._find_all_carica_nodes = MagicMock(return_value=[])

    artifact = svc.execute_pipeline(
        "proj-di",
        run_id="run-di",
        intermediate_mode_override="none",
    )

    assert isinstance(artifact, PipelineArtifact)
    assert artifact.success is True
    assert artifact.result_id == "fake-result"
    assert estrai_executor.execute_calls == [
        (
            ("proj-di", canvas),
            {"run_id": "run-di", "intermediate_mode_override": "none"},
        )
    ]


def test_orchestrator_rejects_missing_executor_dispatch() -> None:
    from app.core.orchestrator.service import OrchestratorService

    with pytest.raises(TypeError, match="estrai_executor"):
        OrchestratorService(  # type: ignore[call-arg]
            project_service=MagicMock(),
            executor=MagicMock(),
            generator=MagicMock(),
            connection_service=MagicMock(),
        )


def test_orchestrator_executes_control_plane_contract_without_project_lookup(
    monkeypatch,
) -> None:
    from app.core.projects.canvas_models import CanvasState
    from app.core.orchestrator.service import OrchestratorService

    project_service = MagicMock()
    project_service.get_project_metadata.side_effect = AssertionError(
        "contract path must not load project metadata"
    )
    project_service.get_project_canvas.side_effect = AssertionError(
        "contract path must not load project canvas"
    )

    estrai_executor = _FakeExecutor()
    svc = OrchestratorService(
        project_service=project_service,
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
        estrai_executor=estrai_executor,
        manipola_executor=_FakeExecutor(),
        enrich_executor=_FakeExecutor(),
    )
    svc._find_all_carica_nodes = MagicMock(return_value=[])

    artifact = svc.execute_pipeline_from_contract(
        project_id="proj-contract",
        project_type="estrai",
        canvas={"nodes": [], "edges": []},
        run_id="run-contract",
    )

    assert artifact.success is True
    args, kwargs = estrai_executor.execute_calls[0]
    assert args[0] == "proj-contract"
    assert isinstance(args[1], CanvasState)
    assert kwargs["run_id"] == "run-contract"
    project_service.get_project_metadata.assert_not_called()
    project_service.get_project_canvas.assert_not_called()


def test_orchestrator_preview_uses_injected_dispatch_executor() -> None:
    from app.core.orchestrator.service import OrchestratorService

    project_service = MagicMock()
    canvas = MagicMock()
    project_service.get_project_metadata.return_value = MagicMock(
        project_type="arricchisci"
    )
    project_service.get_project_canvas.return_value = canvas

    enrich_executor = _FakeExecutor()
    svc = OrchestratorService(
        project_service=project_service,
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
        estrai_executor=_FakeExecutor(),
        manipola_executor=_FakeExecutor(),
        enrich_executor=enrich_executor,
    )

    assert svc.execute_preview("proj-preview", limit=7) == {
        "success": True,
        "rows": [{"id": 1}],
    }
    assert enrich_executor.preview_calls == [(("proj-preview", canvas, 7), {})]


def test_runtime_and_worker_factories_pass_explicit_executors_to_orchestrator() -> None:
    runtime_src = _RUNTIME_SERVICES.read_text(encoding="utf-8")
    worker_src = _PIPELINE_TASK.read_text(encoding="utf-8")

    assert "estrai_executor=estrai_executor" in runtime_src
    assert "manipola_executor=manipola_executor" in runtime_src
    assert "enrich_executor=enrich_executor" in runtime_src
    assert "build_orchestrator_stack().orchestrator" in worker_src
    assert "OrchestratorService(" not in worker_src


def test_orchestrator_has_no_default_executor_builders_left() -> None:
    service_src = _ORCHESTRATOR_SERVICE.read_text(encoding="utf-8")
    carica_task_src = _CARICA_TASK.read_text(encoding="utf-8")

    assert "_build_default_" not in service_src
    assert "build_orchestrator_stack(" in carica_task_src
    assert "OrchestratorService(" not in carica_task_src
