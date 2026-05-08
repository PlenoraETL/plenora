"""Compatibility exports for exceptions that moved to owning domains."""

from __future__ import annotations

from app.core.arricchisci.exceptions import ExecutionContextError
from app.core.files.exceptions import FileSourceResolutionError
from app.core.intermediate.exceptions import IntermediateWriteError
from app.core.projects.exceptions import (
    CanvasCorruptedError,
    ColumnsMetadataSaveError,
    ProjectCleanupPartialError,
    ProjectHasDependenciesError,
)
from app.core.resources.exceptions import MemoryLimitExceededError


__all__ = [
    "CanvasCorruptedError",
    "ColumnsMetadataSaveError",
    "ExecutionContextError",
    "FileSourceResolutionError",
    "IntermediateWriteError",
    "MemoryLimitExceededError",
    "ProjectCleanupPartialError",
    "ProjectHasDependenciesError",
]
