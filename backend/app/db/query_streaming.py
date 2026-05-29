"""Compatibility shim for query streaming helpers."""

from app.core.query_execution.query_streaming import (
    StreamingConnection,
    StreamingEngineFactory,
    execute_streaming_to_writer,
)

__all__ = [
    "StreamingConnection",
    "StreamingEngineFactory",
    "execute_streaming_to_writer",
]
