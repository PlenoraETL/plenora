"""Compatibility wrapper for the neutral REST sink provider boundary."""

from app.core.connections.rest_sink import (
    RestFeatureSink,
    RestJsonValue,
    RestRecord,
    RestScalar,
    RestSinkBatchMode,
    RestSinkBatchResult,
    RestSinkBodyField,
    RestSinkBodyMapping,
    RestSinkConfig,
    RestSinkHeader,
    RestSinkHttpClient,
    RestSinkHttpResponse,
    RestSinkMethod,
    RestSinkRequestSnapshot,
    RestSinkWriteResult,
)

__all__ = [
    "RestFeatureSink",
    "RestJsonValue",
    "RestRecord",
    "RestScalar",
    "RestSinkBatchMode",
    "RestSinkBatchResult",
    "RestSinkBodyField",
    "RestSinkBodyMapping",
    "RestSinkConfig",
    "RestSinkHeader",
    "RestSinkHttpClient",
    "RestSinkHttpResponse",
    "RestSinkMethod",
    "RestSinkRequestSnapshot",
    "RestSinkWriteResult",
]
