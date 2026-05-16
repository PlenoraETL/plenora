"""Execution contract boundary between API control plane and worker data plane."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from app.shared.contracts.payloads import JsonObject


class ProjectExecutionContractService(Protocol):
    """Project service surface required to build a data-plane contract."""

    def get_project_metadata(self, project_id: str) -> Any:
        ...

    def get_project_canvas(self, project_id: str) -> Any:
        ...

    def get_project_columns_metadata(self, project_id: str) -> Any:
        ...


def _as_json_object(value: Any, *, field_name: str) -> JsonObject:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must resolve to a JSON object")
    return dict(value)


def _as_json_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _node_field(node: Mapping[str, Any], field_name: str) -> Any:
    data = node.get("data") or {}
    return node.get(field_name) or (
        data.get(field_name) if isinstance(data, Mapping) else None
    )


def _build_artifact_refs(canvas: JsonObject) -> JsonObject:
    """Extract data dependencies from the canvas without resolving storage."""

    refs: JsonObject = {
        "file_sources": [],
        "project_sources": [],
        "result_refs": [],
    }
    nodes = canvas.get("nodes") or []
    if not isinstance(nodes, list):
        return refs

    for raw_node in nodes:
        if not isinstance(raw_node, Mapping):
            continue
        node_id = str(raw_node.get("id") or "")
        node_type = raw_node.get("type")
        if node_type == "file_source":
            file_source_id = _node_field(raw_node, "file_source_id")
            if file_source_id:
                refs["file_sources"].append(
                    {
                        "node_id": node_id,
                        "file_source_id": str(file_source_id),
                    }
                )
        elif node_type == "project_source":
            source_project_id = _node_field(raw_node, "source_project_id")
            if source_project_id:
                refs["project_sources"].append(
                    {
                        "node_id": node_id,
                        "source_project_id": str(source_project_id),
                    }
                )
        result_id = _node_field(raw_node, "result_id")
        if result_id:
            refs["result_refs"].append(
                {
                    "node_id": node_id,
                    "result_id": str(result_id),
                }
            )

    return refs


def build_pipeline_execution_contract(
    *,
    project_id: str,
    project_service: ProjectExecutionContractService,
) -> JsonObject:
    """Build the complete API-to-worker contract for pipeline execution.

    The API process owns project lookup and tenant metadata resolution. The
    worker receives a self-contained canvas contract so a future Go control
    plane can enqueue Python data-plane work without reusing Python project
    service internals.
    """

    metadata = project_service.get_project_metadata(project_id)
    canvas = project_service.get_project_canvas(project_id)
    if not metadata or not canvas:
        raise LookupError("Progetto non trovato")

    try:
        columns_metadata = project_service.get_project_columns_metadata(project_id)
    except AttributeError:
        columns_metadata = []

    project_type = str(getattr(metadata, "project_type", "") or "")
    if not project_type:
        raise ValueError("project metadata is missing project_type")

    canvas_payload = _as_json_object(canvas, field_name="canvas")
    contract: JsonObject = {
        "schema_version": 1,
        "project_id": project_id,
        "project_type": project_type,
        "group_id": str(getattr(metadata, "group_id", "") or ""),
        "canvas": canvas_payload,
        "columns_metadata": _as_json_list(columns_metadata),
        "artifact_refs": _build_artifact_refs(canvas_payload),
    }

    retention_n = getattr(metadata, "retention_n", None)
    if retention_n is not None:
        contract["retention_n"] = retention_n

    return contract
