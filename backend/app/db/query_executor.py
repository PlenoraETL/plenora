"""Compatibility shim for the query execution capability."""

from app.core.query_execution.query_executor import (
    DataFrameBuilder,
    DatabaseConnection,
    DefaultEngineFactory,
    EngineFactoryProtocol,
    LoggerProtocol,
    NullLogger,
    QueryExecutor,
    QueryPersistenceProtocol,
    QueryResult,
    ResultBuilder,
)

__all__ = [
    "DataFrameBuilder",
    "DatabaseConnection",
    "DefaultEngineFactory",
    "EngineFactoryProtocol",
    "LoggerProtocol",
    "NullLogger",
    "QueryExecutor",
    "QueryPersistenceProtocol",
    "QueryResult",
    "ResultBuilder",
]
