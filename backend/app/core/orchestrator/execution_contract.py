"""Execution contract boundary between API control plane and worker data plane."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.shared.contracts.payloads import JsonObject


class ProjectExecutionContractService(Protocol):
    """Project service surface required to build a data-plane contract."""

    def get_project_metadata(self, project_id: str) -> Any:
        ...

    def get_project_canvas(self, project_id: str) -> Any:
        ...

    def get_project_output_contract(self, project_id: str) -> Any:
        ...


class PipelineExecutionContract(BaseModel):
    """Typed control-plane to data-plane execution contract.

    ``group_id`` exists only at the contract root. Older queued payloads may
    still repeat the same value inside ``runtime_context``: validation accepts
    and removes that duplicate, while a divergent value is rejected.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    project_id: str
    project_type: str
    group_id: str
    canvas: JsonObject
    output_contract: Optional[JsonObject] = None
    artifact_refs: JsonObject = Field(default_factory=dict)
    retention_n: Optional[int] = None
    execution_subject: Optional[JsonObject] = None
    runtime_context: JsonObject = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _canonicalize_tenant(cls, raw: Any) -> Any:
        if not isinstance(raw, Mapping):
            raise ValueError("execution_contract must be a JSON object")
        values = dict(raw)
        group_id = str(values.get("group_id") or "").strip()
        context_raw = values.get("runtime_context") or {}
        if not isinstance(context_raw, Mapping):
            raise ValueError("execution_contract.runtime_context must be a JSON object")
        context = dict(context_raw)
        duplicate_group_id = str(context.pop("group_id", "") or "").strip()
        if duplicate_group_id and duplicate_group_id != group_id:
            raise ValueError(
                "execution_contract.runtime_context.group_id conflicts with group_id"
            )
        values["group_id"] = group_id
        values["runtime_context"] = context
        return values

    @field_validator("project_id", "project_type", "group_id")
    @classmethod
    def _required_text(cls, value: str, info: ValidationInfo) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"execution_contract.{info.field_name} is required")
        return normalized

    @classmethod
    def from_payload(
        cls,
        raw: Any,
        *,
        expected_project_id: Optional[str] = None,
    ) -> "PipelineExecutionContract":
        if not isinstance(raw, Mapping):
            raise ValueError("execution_contract must be a JSON object")
        values = dict(raw)
        if expected_project_id and not values.get("project_id"):
            values["project_id"] = expected_project_id
        contract = cls.model_validate(values)
        if expected_project_id and contract.project_id != expected_project_id:
            raise ValueError("execution_contract project_id mismatch")
        return contract

    def to_payload(self) -> JsonObject:
        """Serialize only queue-bound fields, omitting absent optional data."""
        payload: JsonObject = {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "project_type": self.project_type,
            "group_id": self.group_id,
            "canvas": dict(self.canvas),
            "output_contract": (
                dict(self.output_contract)
                if self.output_contract is not None
                else None
            ),
            "artifact_refs": dict(self.artifact_refs),
        }
        if self.retention_n is not None:
            payload["retention_n"] = self.retention_n
        if self.execution_subject is not None:
            payload["execution_subject"] = dict(self.execution_subject)
        if self.runtime_context:
            payload["runtime_context"] = dict(self.runtime_context)
        return payload

    def build_runtime_context(
        self,
        *,
        output_task_id: Optional[str] = None,
    ) -> JsonObject:
        """Build the executor context from the single canonical tenant."""
        context = dict(self.runtime_context)
        context["group_id"] = self.group_id
        if output_task_id:
            context["output_task_id"] = output_task_id
        return context

    @property
    def executable_subject(self) -> Optional[JsonObject]:
        subject = self.execution_subject
        if subject and subject.get("kind") == "eseguibile":
            return dict(subject)
        return None


def _as_json_object(value: Any, *, field_name: str) -> JsonObject:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must resolve to a JSON object")
    return dict(value)


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
) -> PipelineExecutionContract:
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

    output_contract = project_service.get_project_output_contract(project_id)

    project_type = str(getattr(metadata, "project_type", "") or "")
    if not project_type:
        raise ValueError("project metadata is missing project_type")

    canvas_payload = _as_json_object(canvas, field_name="canvas")
    retention_n = getattr(metadata, "retention_n", None)
    return PipelineExecutionContract(
        schema_version=1,
        project_id=project_id,
        project_type=project_type,
        group_id=str(getattr(metadata, "group_id", "") or ""),
        canvas=canvas_payload,
        output_contract=(
            dict(output_contract)
            if isinstance(output_contract, Mapping)
            else None
        ),
        artifact_refs=_build_artifact_refs(canvas_payload),
        retention_n=retention_n,
    )
