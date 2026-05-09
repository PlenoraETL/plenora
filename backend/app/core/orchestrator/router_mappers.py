"""HTTP response mappers for orchestrator task routes."""

from __future__ import annotations

from decimal import Decimal
import math
from typing import Any, Dict, Optional, TypedDict, cast

from app.core.orchestrator.task_service import TaskInfo, TaskResultPayload


class TaskInfoPublicPayload(TypedDict):
    task_id: str
    task_type: str
    project_id: str
    status: str
    progress: int
    message: str
    result: Dict[str, Any] | None
    error: str | None
    created_at: str | None
    started_at: str | None
    completed_at: str | None


def task_result_to_dict(result: object) -> Dict[str, Any]:
    """Convert typed task result metadata at the HTTP/compat boundary."""
    if result is None:
        return {}
    if isinstance(result, TaskResultPayload):
        return {field.name: field.value for field in result.public_fields}
    if isinstance(result, dict):
        return dict(result)
    return {}


def task_info_to_dict(task_info: TaskInfo) -> TaskInfoPublicPayload:
    """Serialize typed task info at the HTTP boundary."""
    if not isinstance(task_info, TaskInfo):
        legacy_to_dict = getattr(task_info, "to_dict", None)
        if callable(legacy_to_dict):
            return cast(TaskInfoPublicPayload, dict(legacy_to_dict()))
    return {
        "task_id": task_info.task_id,
        "task_type": task_info.task_type.value,
        "project_id": task_info.project_id,
        "status": task_info.status.value,
        "progress": task_info.progress,
        "message": task_info.message,
        "result": task_result_to_dict(task_info.result) if task_info.result else None,
        "error": task_info.error,
        "created_at": task_info.created_at,
        "started_at": task_info.started_at,
        "completed_at": task_info.completed_at,
    }


def task_result_project_id(result: object) -> Optional[str]:
    if isinstance(result, TaskResultPayload):
        return result.project_id or None
    if isinstance(result, dict):
        value = result.get("project_id")
        return str(value) if value is not None else None
    return None


def sanitize_task_status_payload(value: object) -> object:
    """Convert recursively non-JSON-serializable values to native Python types."""
    if isinstance(value, dict):
        return {
            key: sanitize_task_status_payload(nested_value)
            for key, nested_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_task_status_payload(nested_value) for nested_value in value]
    if isinstance(value, Decimal):
        number_value = float(value)
        return int(number_value) if number_value == int(number_value) else number_value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        if value == int(value):
            return int(value)
        return value

    type_name = type(value).__name__
    if type_name in ("NaTType", "NaT"):
        return None
    if type_name == "Timestamp":
        return value.isoformat()
    if type_name == "date":
        return value.isoformat()
    if type_name.startswith("int") and type_name != "int":
        return int(value)
    if type_name.startswith("float") and type_name != "float":
        return float(value)
    return value
