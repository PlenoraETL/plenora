"""Typed query execution/session context helpers.

This module is intentionally small and independent from ``QueryExecutor`` so
timeout/session behavior can move toward one shared provider abstraction
without changing the public executor API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, List, Mapping, Optional, Protocol

from sqlalchemy import text

from app.db.sql_types import QueryParams


class QueryConnection(Protocol):
    """Connection shape needed by query engine/session helpers."""

    type: str
    host: str
    port: int
    database: str
    username: str
    password: str


class SessionLogger(Protocol):
    """Minimal logger surface used by best-effort session configuration."""

    def debug(self, msg: str, *args: Any) -> None: ...


@dataclass(frozen=True)
class SessionContext:
    """Provider-specific session metadata for a query execution."""

    connection: QueryConnection
    provider: str
    timeout_seconds: int
    timeout_milliseconds: int
    statement_timeout_sql: Optional[str] = None
    uses_native_call_timeout: bool = False
    oracle_open_cursors: Optional[int] = None

    @classmethod
    def for_connection(
        cls,
        connection: QueryConnection,
        *,
        timeout_seconds: int,
    ) -> "SessionContext":
        provider = (connection.type or "").lower()
        timeout_s = int(timeout_seconds)
        timeout_ms = timeout_s * 1000

        statement_timeout_sql = None
        if timeout_s > 0 and provider in ("postgres", "postgresql"):
            statement_timeout_sql = "SET statement_timeout = :t"
        elif timeout_s > 0 and provider == "mysql":
            statement_timeout_sql = "SET SESSION max_execution_time = :t"

        return cls(
            connection=connection,
            provider=provider,
            timeout_seconds=timeout_s,
            timeout_milliseconds=timeout_ms,
            statement_timeout_sql=statement_timeout_sql,
            uses_native_call_timeout=provider == "oracle" and timeout_s > 0,
            oracle_open_cursors=32767 if provider == "oracle" else None,
        )


@dataclass(frozen=True)
class ExecutionContext:
    """Typed metadata for one query execution."""

    session: SessionContext
    sql: str
    params: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def connection(self) -> QueryConnection:
        return self.session.connection

    @classmethod
    def for_query(
        cls,
        *,
        connection: QueryConnection,
        sql: str,
        params: Optional[QueryParams],
        timeout_seconds: int,
    ) -> "ExecutionContext":
        session = SessionContext.for_connection(
            connection,
            timeout_seconds=timeout_seconds,
        )
        frozen_params: Mapping[str, Any]
        if params:
            frozen_params = MappingProxyType(dict(params))
        else:
            frozen_params = MappingProxyType({})
        return cls(session=session, sql=sql, params=frozen_params)


def build_execution_context(
    *,
    connection: QueryConnection,
    sql: str,
    params: Optional[QueryParams],
    logger: SessionLogger,
) -> ExecutionContext:
    """Build typed execution/session metadata from runtime settings."""
    try:
        from app.config import settings

        timeout_seconds = int(settings.DB_QUERY_TIMEOUT)
    except Exception as e:
        timeout_seconds = 0
        logger.debug(
            "lettura DB_QUERY_TIMEOUT fallita, timeout sessione disabilitato: %s: %s",
            type(e).__name__,
            e,
        )
    return ExecutionContext.for_query(
        connection=connection,
        sql=sql,
        params=params,
        timeout_seconds=timeout_seconds,
    )


def apply_session_context(
    sqlalchemy_conn: Any,
    context: SessionContext,
    logger: SessionLogger,
) -> None:
    """Apply provider session settings using typed session metadata.

    All operations are best effort, preserving the historical behavior: failure
    to set a provider timeout/session knob is logged and query execution
    continues, leaving the worker-level timeout as fallback.
    """
    try:
        if context.statement_timeout_sql:
            sqlalchemy_conn.execute(
                text(context.statement_timeout_sql),
                {"t": context.timeout_milliseconds},
            )
        elif context.uses_native_call_timeout:
            raw = sqlalchemy_conn.connection.dbapi_connection
            if hasattr(raw, "callTimeout"):
                raw.callTimeout = context.timeout_milliseconds
    except Exception as e:
        logger.debug(
            "set timeout di sessione fallito (db=%s), uso fallback Celery: %s: %s",
            context.provider,
            type(e).__name__,
            e,
        )

    if context.oracle_open_cursors is not None:
        try:
            sqlalchemy_conn.execute(
                text(
                    "ALTER SESSION SET OPEN_CURSORS = "
                    f"{context.oracle_open_cursors}"
                )
            )
        except Exception as e:
            logger.debug(
                "ALTER SESSION OPEN_CURSORS fallito (probabili permessi), uso default: %s: %s",
                type(e).__name__,
                e,
            )


def normalize_result_columns(
    columns: Iterable[str],
    context: SessionContext,
) -> List[str]:
    """Return result column names with provider-specific normalization."""
    if context.provider == "oracle":
        return [column.lower() for column in columns]
    return list(columns)
