"""Canonical query execution capability.

The low-level ``app.db`` package owns the application PostgreSQL boundary and
SQL utility functions. Multi-database query execution lives here because it
orchestrates dataframe, result, and intermediate-resource boundaries.
"""

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
