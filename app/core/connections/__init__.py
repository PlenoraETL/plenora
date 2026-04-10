"""
Modulo Connections — CRUD connessioni database.

Gestisce le connessioni a PostgreSQL, MySQL, SQL Server, Oracle, SQLite.
Usato dal modulo ESTRAI per configurare le sorgenti dati.
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
