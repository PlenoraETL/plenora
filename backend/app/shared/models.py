"""Compatibility shim for canvas/project models.

Runtime code must import these models from ``app.core.projects.canvas_models``.
This module remains only for external compatibility and older tests.
"""

from app.core.projects.canvas_models import (
    AggregationConfig,
    CanvasState,
    Column,
    ColumnInfo,
    ColumnType,
    Edge,
    ExecuteRequest,
    Filter,
    HavingCondition,
    Node,
    OrderByConfig,
    Position,
    ValidationError,
    ValidationResult,
)

__all__ = [
    "AggregationConfig",
    "CanvasState",
    "Column",
    "ColumnInfo",
    "ColumnType",
    "Edge",
    "ExecuteRequest",
    "Filter",
    "HavingCondition",
    "Node",
    "OrderByConfig",
    "Position",
    "ValidationError",
    "ValidationResult",
]
