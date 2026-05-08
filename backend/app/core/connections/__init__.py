"""
Modulo Connections — CRUD connessioni database.

Gestisce le connessioni a PostgreSQL, MySQL, SQL Server, Oracle, SQLite.
"""

from .models import (
    DatabaseConnection,
    ConnectionCreate,
    ConnectionUpdate,
    ConnectionList,
    ConnectionTestResult,
)

__all__ = [
    "DatabaseConnection",
    "ConnectionCreate",
    "ConnectionUpdate",
    "ConnectionList",
    "ConnectionTestResult",
    "ConnectionService",
    "_sanitize_mssql_error",
]


def __getattr__(name: str) -> object:
    if name in {"ConnectionService", "_sanitize_mssql_error"}:
        from .service import ConnectionService, _sanitize_mssql_error

        values: dict[str, object] = {
            "ConnectionService": ConnectionService,
            "_sanitize_mssql_error": _sanitize_mssql_error,
        }
        return values[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
