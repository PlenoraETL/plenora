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
]
