"""Streaming writers for database query results.

This module owns the CSV/Parquet zero-full-materialization path used by
QueryExecutor. It intentionally does not import QueryExecutor, so the large
executor module can delegate streaming without creating a reverse dependency.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from typing import Any, BinaryIO, List, Optional, Protocol, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import settings
from app.core.dataframes.tabular import dataframe_from_rows, make_unique_column_names
from app.db.sql_types import QueryParams
from app.utils.logger import get_logger


logger = get_logger(__name__)

CHUNK_SIZE = 10_000


class StreamingConnection(Protocol):
    type: str


class StreamingEngineFactory(Protocol):
    def create_engine(self, conn: StreamingConnection) -> Engine: ...


# Streaming canonico: `execute_streaming_to_writer(file-like)` orchestrato da
# `persist_streaming_query_to_storage`.


def _max_streaming_rows() -> int:
    return int(getattr(settings, "ESTRAI_MAX_ROWS", 5_000_000) or 0)


def _raise_if_over_row_cap(total_rows: int, max_rows: int) -> None:
    if max_rows > 0 and total_rows > max_rows:
        raise ValueError(
            f"Query ha ritornato piu' di {max_rows:,} "
            f"righe (streaming). Aggiungi WHERE/LIMIT "
            f"o alza ESTRAI_MAX_ROWS."
        )


def _configure_session(conn: Any, connection: StreamingConnection) -> None:
    """Apply the same best-effort session settings used by QueryExecutor."""
    try:
        timeout_s = settings.DB_QUERY_TIMEOUT
        timeout_ms = int(timeout_s) * 1000
        # Literal embedded (no bind param) per i SET utility command:
        # PostgreSQL non accetta bound parameter qui. `timeout_ms` e' un int
        # da settings, quindi non introduce injection.
        if connection.type in ("postgres", "postgresql"):
            conn.execute(text(f"SET statement_timeout = {timeout_ms}"))
        elif connection.type == "mysql":
            conn.execute(text(f"SET SESSION max_execution_time = {timeout_ms}"))
        elif connection.type == "oracle":
            raw = conn.connection.dbapi_connection
            if hasattr(raw, "callTimeout"):
                raw.callTimeout = timeout_ms
    except Exception as e:
        logger.debug(
            "set timeout di sessione fallito (db=%s), uso fallback Celery: %s: %s",
            connection.type,
            type(e).__name__,
            e,
        )

    if connection.type == "oracle":
        try:
            conn.execute(text("ALTER SESSION SET OPEN_CURSORS = 32767"))
        except Exception as e:
            logger.debug(
                "ALTER SESSION OPEN_CURSORS fallito (probabili permessi), uso default: %s: %s",
                type(e).__name__,
                e,
            )


def _columns_from_result(result: Any, connection: StreamingConnection) -> List[str]:
    columns = [desc[0] for desc in result.cursor.description]
    if connection.type == "oracle":
        columns = [c.lower() for c in columns]
    unique_columns, _renames = make_unique_column_names(columns)
    return unique_columns


def _compact_decimal(value: Decimal) -> str:
    """Return a compact decimal string without converting through float."""
    if not value.is_finite():
        return str(value)
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _normalize_decimal(value: Any) -> Any:
    """Compact Decimal values for CSV streaming while preserving precision."""
    if isinstance(value, Decimal):
        return _compact_decimal(value)
    return value


def _normalize_row(row: Any) -> tuple:
    return tuple(_normalize_decimal(v) for v in row)


def _write_csv_to_writer(
    result: Any, columns: List[str], writer: BinaryIO, max_rows: int,
) -> int:
    """Write CSV rows da cursor a `writer` (binary file-like).

    `csv.writer` richiede un text file-like; wrappiamo `writer` in
    `io.TextIOWrapper(..., write_through=True)` per pass-through delle
    write su underlying binary stream. `detach()` nel finally evita
    di chiudere il writer underlying (caller-managed lifecycle).
    """
    text_target = io.TextIOWrapper(
        writer,  # type: ignore[arg-type]
        encoding="utf-8",
        newline="",
        write_through=True,
    )
    total_rows = 0
    try:
        cw = csv.writer(text_target)
        cw.writerow(columns)
        while True:
            chunk = result.fetchmany(CHUNK_SIZE)
            if not chunk:
                break
            cw.writerows(_normalize_row(r) for r in chunk)
            total_rows += len(chunk)
            _raise_if_over_row_cap(total_rows, max_rows)
        text_target.flush()
    finally:
        # detach() restituisce il buffer underlying SENZA chiuderlo.
        # Il caller (StreamingResultBlobWriter o legacy file) chiude.
        try:
            text_target.detach()
        except Exception:
            pass
    return total_rows


def _write_parquet_to_writer(
    result: Any, columns: List[str], writer: BinaryIO, max_rows: int,
) -> int:
    """Write Parquet record batches da cursor a `writer` (binary file-like).

    PyArrow `ParquetWriter` accetta file-like writable. Footer
    accumulato in-memory + flushed at close(): nessuna seek
    necessaria (multipart-friendly).
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    total_rows = 0
    pq_writer = None
    try:
        while True:
            chunk = result.fetchmany(CHUNK_SIZE)
            if not chunk:
                break
            df_chunk = dataframe_from_rows(chunk, columns)
            table = pa.Table.from_pandas(df_chunk, preserve_index=False)
            if pq_writer is None:
                pq_writer = pq.ParquetWriter(writer, table.schema)
            pq_writer.write_table(table)
            total_rows += len(chunk)
            _raise_if_over_row_cap(total_rows, max_rows)
    finally:
        if pq_writer is not None:
            pq_writer.close()
    return total_rows


def execute_streaming_to_writer(
    *,
    engine_factory: StreamingEngineFactory,
    connection: StreamingConnection,
    sql: str,
    params: Optional[QueryParams],
    output_writer: BinaryIO,
    output_format: str,
) -> Tuple[int, List[str]]:
    """Execute a query streaming CSV/Parquet bytes to `output_writer`.

    Sostituisce `execute_streaming_to_file` per i caller storage-first:
    invece di scrivere su un path filesystem, scrive su un file-like
    writable (tipicamente `StreamingResultBlobWriter` per S3 multipart
    o `io.BytesIO` per testing).
    """
    engine = engine_factory.create_engine(connection)
    max_rows = _max_streaming_rows()

    with engine.connect() as conn:
        _configure_session(conn, connection)
        result = conn.execute(text(sql), params or {})
        columns = _columns_from_result(result, connection)

        if output_format == "csv":
            total_rows = _write_csv_to_writer(result, columns, output_writer, max_rows)
        elif output_format == "parquet":
            total_rows = _write_parquet_to_writer(result, columns, output_writer, max_rows)
        else:
            raise ValueError(
                f"execute_streaming_to_writer: formato non supportato "
                f"per streaming: {output_format}. Use csv o parquet."
            )

    return total_rows, columns


# `execute_streaming_to_writer(file-like)` accetta qualunque binary writer.
# Per persistere su storage canonical
# il caller usa `StreamingResultBlobWriter` (S3 multipart o DB BYTEA);
# per test bytes-only basta `io.BytesIO()`.
