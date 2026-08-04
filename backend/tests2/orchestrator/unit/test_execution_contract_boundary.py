"""Contract tests for the API-to-worker pipeline execution contract."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests2.shared._ast_identifiers import all_identifiers


pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.contract]


class _ProjectService:
    def get_project_metadata(self, project_id: str):
        assert project_id == "project-1"
        return SimpleNamespace(
            project_type="manipola",
            group_id="group-1",
            retention_n=7,
        )

    def get_project_canvas(self, project_id: str):
        assert project_id == "project-1"
        return SimpleNamespace(
            model_dump=lambda: {
                "nodes": [
                    {
                        "id": "file-1",
                        "type": "file_source",
                        "file_source_id": "fs-1",
                    },
                    {
                        "id": "project-1",
                        "type": "project_source",
                        "data": {"source_project_id": "upstream-1"},
                    },
                    {
                        "id": "result-1",
                        "type": "result",
                        "data": {"result_id": "res-1"},
                    },
                ],
                "edges": [],
            }
        )

    def get_project_output_contract(self, project_id: str):
        assert project_id == "project-1"
        return {
            "schema_version": 1,
            "producer_project_id": "project-1",
            "revision": 1,
            "columns": [
                {
                    "id": "col-city",
                    "source_name": "city",
                    "physical_name": "city",
                    "display_name": "city",
                    "type": {"logical": "string", "nullable": True},
                    "ordinal": 0,
                    "status": "active",
                }
            ],
        }


def test_build_pipeline_execution_contract_is_self_contained():
    from app.core.orchestrator.execution_contract import (
        build_pipeline_execution_contract,
    )

    contract = build_pipeline_execution_contract(
        project_id="project-1",
        project_service=_ProjectService(),
    )

    assert contract.to_payload() == {
        "schema_version": 1,
        "project_id": "project-1",
        "project_type": "manipola",
        "group_id": "group-1",
        "canvas": {
            "nodes": [
                {
                    "id": "file-1",
                    "type": "file_source",
                    "file_source_id": "fs-1",
                },
                {
                    "id": "project-1",
                    "type": "project_source",
                    "data": {"source_project_id": "upstream-1"},
                },
                {
                    "id": "result-1",
                    "type": "result",
                    "data": {"result_id": "res-1"},
                },
            ],
            "edges": [],
        },
        "output_contract": {
            "schema_version": 1,
            "producer_project_id": "project-1",
            "revision": 1,
            "columns": [
                {
                    "id": "col-city",
                    "source_name": "city",
                    "physical_name": "city",
                    "display_name": "city",
                    "type": {"logical": "string", "nullable": True},
                    "ordinal": 0,
                    "status": "active",
                }
            ],
        },
        "artifact_refs": {
            "file_sources": [
                {"node_id": "file-1", "file_source_id": "fs-1"},
            ],
            "project_sources": [
                {"node_id": "project-1", "source_project_id": "upstream-1"},
            ],
            "result_refs": [
                {"node_id": "result-1", "result_id": "res-1"},
            ],
        },
        "retention_n": 7,
    }


def test_pipeline_execution_contract_fails_for_missing_project():
    from app.core.orchestrator.execution_contract import (
        build_pipeline_execution_contract,
    )

    class MissingProjectService(_ProjectService):
        def get_project_metadata(self, project_id: str):
            return None

    with pytest.raises(LookupError):
        build_pipeline_execution_contract(
            project_id="project-1",
            project_service=MissingProjectService(),
        )


def test_pipeline_worker_preserves_contract_schema_extensions():
    from workers.tasks.pipeline_task import _normalize_execution_contract

    normalized = _normalize_execution_contract(
        {
            "schema_version": 1,
            "project_id": "project-1",
            "project_type": "manipola",
            "group_id": "group-1",
            "canvas": {"nodes": [], "edges": []},
            "output_contract": {
                "columns": [
                    {
                        "id": "col-city",
                        "source_name": "city",
                        "physical_name": "city",
                        "display_name": "city",
                        "type": {"logical": "string"},
                        "ordinal": 0,
                        "status": "active",
                    }
                ]
            },
            "artifact_refs": {"source-1": "s3://bucket/key"},
            "retention_n": 3,
        },
        expected_project_id="project-1",
    )

    assert normalized.to_payload() == {
        "schema_version": 1,
        "project_id": "project-1",
        "project_type": "manipola",
        "canvas": {"nodes": [], "edges": []},
        "group_id": "group-1",
        "artifact_refs": {"source-1": "s3://bucket/key"},
        "output_contract": {
            "columns": [
                {
                    "id": "col-city",
                    "source_name": "city",
                    "physical_name": "city",
                    "display_name": "city",
                    "type": {"logical": "string"},
                    "ordinal": 0,
                    "status": "active",
                }
            ]
        },
        "retention_n": 3,
    }


def test_pipeline_worker_rejects_contract_without_tenant():
    from workers.tasks.pipeline_task import _normalize_execution_contract

    with pytest.raises(
        ValueError,
        match="execution_contract.group_id is required",
    ):
        _normalize_execution_contract(
            {
                "schema_version": 1,
                "project_id": "project-1",
                "project_type": "manipola",
                "canvas": {"nodes": [], "edges": []},
            },
            expected_project_id="project-1",
        )


def test_pipeline_contract_rejects_divergent_runtime_tenant():
    from app.core.orchestrator.execution_contract import (
        PipelineExecutionContract,
    )

    with pytest.raises(ValueError, match="conflicts with group_id"):
        PipelineExecutionContract.from_payload(
            {
                "project_id": "project-1",
                "project_type": "manipola",
                "group_id": "group-1",
                "canvas": {"nodes": [], "edges": []},
                "runtime_context": {"group_id": "group-2"},
            }
        )


def test_pipeline_contract_migrates_equal_legacy_runtime_tenant():
    from app.core.orchestrator.execution_contract import (
        PipelineExecutionContract,
    )

    contract = PipelineExecutionContract.from_payload(
        {
            "project_id": "project-1",
            "project_type": "manipola",
            "group_id": "group-1",
            "canvas": {"nodes": [], "edges": []},
            "runtime_context": {
                "group_id": "group-1",
                "output_format": "xlsx",
            },
        }
    )

    assert "group_id" not in contract.runtime_context
    assert contract.build_runtime_context() == {
        "group_id": "group-1",
        "output_format": "xlsx",
        "persist_contract": True,
    }


def test_pipeline_contract_rejects_unknown_schema_version():
    from app.core.orchestrator.execution_contract import (
        PipelineExecutionContract,
    )

    with pytest.raises(ValueError, match="schema_version non supportata"):
        PipelineExecutionContract.from_payload(
            {
                "schema_version": 99,
                "project_id": "project-1",
                "project_type": "manipola",
                "group_id": "group-1",
                "canvas": {"nodes": [], "edges": []},
            }
        )


def test_carica_contract_roundtrip_preserves_tenant_and_write_snapshot():
    from app.core.orchestrator.execution_contract import CaricaExecutionContract

    payload = {
        "schema_version": 1,
        "project_id": "project-1",
        "project_type": "manipola",
        "group_id": "group-1",
        "canvas": {"nodes": [], "edges": []},
        "carica_node_data": {
            "connection_id": "conn-1",
            "schema_name": "public",
            "table_name": "dest",
        },
        "output_format": "xlsx",
        "force_recalculate": True,
        "triggered_by": "cron",
    }

    contract = CaricaExecutionContract.from_payload(
        payload,
        expected_project_id="project-1",
    )

    assert contract.to_payload() == payload
    upstream = contract.to_pipeline_contract()
    assert upstream.project_id == "project-1"
    assert upstream.group_id == "group-1"
    assert upstream.canvas == {"nodes": [], "edges": []}


def test_carica_contract_rejects_project_mismatch_and_missing_tenant():
    from app.core.orchestrator.execution_contract import CaricaExecutionContract

    base = {
        "project_id": "project-1",
        "project_type": "manipola",
        "group_id": "group-1",
        "canvas": {"nodes": [], "edges": []},
        "carica_node_data": {"table_name": "dest"},
    }
    with pytest.raises(ValueError, match="project_id mismatch"):
        CaricaExecutionContract.from_payload(
            base,
            expected_project_id="project-2",
        )
    with pytest.raises(ValueError, match="group_id is required"):
        CaricaExecutionContract.from_payload({**base, "group_id": ""})


def test_preview_contract_threads_canonical_tenant_without_project_lookup():
    from unittest.mock import MagicMock

    from app.core.orchestrator.execution_contract import (
        PipelineExecutionContract,
    )
    from app.core.orchestrator.service import OrchestratorService

    class _PreviewExecutor:
        def __init__(self) -> None:
            self.calls = []

        def preview(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return {"success": True, "rows": []}

        def execute(self, *args, **kwargs):
            return {"success": True}

    executor = _PreviewExecutor()
    project_service = MagicMock()
    orchestrator = OrchestratorService(
        project_service=project_service,
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
        estrai_executor=executor,
        manipola_executor=executor,
        enrich_executor=executor,
    )
    contract = PipelineExecutionContract(
        project_id="project-1",
        project_type="manipola",
        group_id="group-1",
        canvas={"nodes": [], "edges": []},
    )

    result = orchestrator.execute_preview_from_contract(contract, limit=25)

    assert result["success"] is True
    assert executor.calls[0][1]["execution_context"] == {
        "group_id": "group-1",
    }
    project_service.get_project_metadata.assert_not_called()
    project_service.get_project_canvas.assert_not_called()


def test_pipeline_contract_roundtrip_keeps_tenant_through_executor():
    """API build -> queue payload -> worker parse -> MANIPOLA context."""
    from unittest.mock import MagicMock

    from app.core.orchestrator.execution_contract import (
        build_pipeline_execution_contract,
    )
    from app.core.orchestrator.service import OrchestratorService
    from app.shared.execution.executor_protocol import ExecutionResult
    from workers.tasks.pipeline_task import _normalize_execution_contract

    class _RoundTripProjectService(_ProjectService):
        def get_project_canvas(self, project_id: str):
            assert project_id == "project-1"
            return SimpleNamespace(
                model_dump=lambda: {"nodes": [], "edges": []},
            )

    built = build_pipeline_execution_contract(
        project_id="project-1",
        project_service=_RoundTripProjectService(),
    )
    payload = built.to_payload()
    assert payload["group_id"] == "group-1"
    assert "group_id" not in payload.get("runtime_context", {})

    parsed = _normalize_execution_contract(
        payload,
        expected_project_id="project-1",
    )
    class _Executor:
        def __init__(self) -> None:
            self.calls = []

        def execute(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return ExecutionResult(success=True, payload={"rows_count": 1})

        def preview(self, *args, **kwargs):
            return {"success": True, "rows": []}

    manipola_executor = _Executor()
    other_executor = _Executor()
    project_service = MagicMock()
    orchestrator = OrchestratorService(
        project_service=project_service,
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
        estrai_executor=other_executor,
        manipola_executor=manipola_executor,
        enrich_executor=other_executor,
    )
    orchestrator._execute_carica_if_present = MagicMock(return_value=None)

    result = orchestrator.execute_pipeline_from_contract(contract=parsed)

    assert result.success is True
    assert manipola_executor.calls[0][1]["execution_context"] == {
        "group_id": "group-1",
    }
    project_service.get_project_metadata.assert_not_called()
    project_service.get_project_canvas.assert_not_called()


def test_scheduler_pipeline_path_uses_execution_contract_boundary():
    from pathlib import Path

    backend_root = Path(__file__).resolve().parents[3]
    source = (
        backend_root
        / "app"
        / "core"
        / "scheduler"
        / "execution_manager.py"
    ).read_text(encoding="utf-8")

    # AST-bound: l'execution_manager deve usare il boundary contract (entrambi
    # identificatori-funzione), robusto a menzioni in commenti/stringhe.
    identifiers = all_identifiers(source)
    assert "build_pipeline_execution_contract" in identifiers
    assert "execute_pipeline_from_contract" in identifiers
