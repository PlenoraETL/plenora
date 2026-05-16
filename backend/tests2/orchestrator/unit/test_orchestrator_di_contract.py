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


def test_orchestrator_accepts_injected_executors_without_default_construction(
    monkeypatch,
) -> None:
    """Injected executor protocols are enough to run, no concrete builders needed."""
    from app.core.artifacts.types import PipelineArtifact
    from app.core.orchestrator.service import OrchestratorService

    def _forbidden_default_builder(self):
        raise AssertionError("OrchestratorService constructed a concrete executor")

    monkeypatch.setattr(
        OrchestratorService,
        "_build_default_estrai_executor",
        _forbidden_default_builder,
    )
    monkeypatch.setattr(
        OrchestratorService,
        "_build_default_manipola_executor",
        _forbidden_default_builder,
    )
    monkeypatch.setattr(
        OrchestratorService,
        "_build_default_enrich_executor",
        _forbidden_default_builder,
    )

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


def test_orchestrator_preview_uses_injected_dispatch_executor(monkeypatch) -> None:
    from app.core.orchestrator.service import OrchestratorService

    monkeypatch.setattr(
        OrchestratorService,
        "_build_default_estrai_executor",
        lambda self: pytest.fail("default estrai builder called"),
    )
    monkeypatch.setattr(
        OrchestratorService,
        "_build_default_manipola_executor",
        lambda self: pytest.fail("default manipola builder called"),
    )
    monkeypatch.setattr(
        OrchestratorService,
        "_build_default_enrich_executor",
        lambda self: pytest.fail("default enrich builder called"),
    )

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

    for src, label in (
        (runtime_src, "app/runtime/services.py"),
        (worker_src, "workers/tasks/pipeline_task.py"),
    ):
        assert "estrai_executor=estrai_executor" in src, label
        assert "manipola_executor=manipola_executor" in src, label
        assert "enrich_executor=enrich_executor" in src, label
