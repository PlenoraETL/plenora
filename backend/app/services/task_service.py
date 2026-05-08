"""KEEP_COMPAT wrapper for the orchestrator-owned task service boundary.

Canonical implementation: app.core.orchestrator.task_service.
"""

from app.core.orchestrator.task_service import (
    TaskInfo,
    TaskResultPayload,
    TaskService,
    TaskStatus,
    TaskType,
    task_service,
)

__all__ = [
    "TaskInfo",
    "TaskResultPayload",
    "TaskService",
    "TaskStatus",
    "TaskType",
    "task_service",
]
