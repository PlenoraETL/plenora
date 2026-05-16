"""Contract tests for the API-to-worker pipeline execution contract."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


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

    def get_project_columns_metadata(self, project_id: str):
        assert project_id == "project-1"
        return [{"name": "city", "type": "str"}]


def test_build_pipeline_execution_contract_is_self_contained():
    from app.core.orchestrator.execution_contract import (
        build_pipeline_execution_contract,
    )

    contract = build_pipeline_execution_contract(
        project_id="project-1",
        project_service=_ProjectService(),
    )

    assert contract == {
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
        "columns_metadata": [{"name": "city", "type": "str"}],
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
            "columns_metadata": [{"name": "city", "type": "str"}],
            "artifact_refs": {"source-1": "s3://bucket/key"},
            "retention_n": 3,
        },
        expected_project_id="project-1",
    )

    assert normalized == {
        "schema_version": 1,
        "project_id": "project-1",
        "project_type": "manipola",
        "canvas": {"nodes": [], "edges": []},
        "group_id": "group-1",
        "artifact_refs": {"source-1": "s3://bucket/key"},
        "columns_metadata": [{"name": "city", "type": "str"}],
        "retention_n": 3,
    }


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

    assert "build_pipeline_execution_contract" in source
    assert "execute_pipeline_from_contract" in source
