"""Compatibility shim for query result persistence."""

from app.core.query_execution.query_result_persistence import (
    PersistedQueryResult,
    QueryResultPersistence,
    StreamedQueryResult,
)
from app.core.results.pipeline_result import save_pipeline_result_outcome

__all__ = [
    "PersistedQueryResult",
    "QueryResultPersistence",
    "StreamedQueryResult",
    "save_pipeline_result_outcome",
]
