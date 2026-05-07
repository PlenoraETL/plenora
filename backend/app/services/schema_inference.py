"""KEEP_COMPAT wrapper for project schema inference.

Canonical target: app.core.projects.schema_inference.
"""

from app.core.projects.schema_inference import (
    ColumnSchema,
    ConnectionValidator,
    SchemaInferenceService,
)

__all__ = [
    "ColumnSchema",
    "ConnectionValidator",
    "SchemaInferenceService",
]
