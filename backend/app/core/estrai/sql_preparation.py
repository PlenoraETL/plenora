"""Resolve SQL compilation options from the connection used for execution."""
from typing import Any

SqlConnectionOptions = dict[str, str | None]


def sql_connection_options(connection: Any) -> SqlConnectionOptions:
    provider = getattr(connection, "type", None) or getattr(connection, "db_type", None)
    if not isinstance(provider, str) or not provider:
        raise ValueError("Provider SQL non leggibile")
    return {"db_type": provider, "db_schema": getattr(connection, "db_schema", None)}
