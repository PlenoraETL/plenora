"""
Shared execution contracts - engine-agnostic canvas execution types.

Leaf package: no imports from ``app.core.*``, ``app.db.*``, or heavy
"""

from app.shared.execution.schema_types import ColumnSchema

__all__ = ["ColumnSchema"]
