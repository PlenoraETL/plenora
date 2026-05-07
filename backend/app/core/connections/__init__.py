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
from .service import ConnectionService, _sanitize_mssql_error

__all__ = [
    "DatabaseConnection",
    "ConnectionCreate",
    "ConnectionUpdate",
    "ConnectionList",
    "ConnectionTestResult",
    "ConnectionService",
    "_sanitize_mssql_error",
]
