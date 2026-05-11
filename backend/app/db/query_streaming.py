"""Streaming writers for database query results.

This module owns the CSV/Parquet zero-full-materialization path used by
QueryExecutor. It intentionally does not import QueryExecutor, so the large
executor module can delegate streaming without creating a reverse dependency.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import settings
from app.utils.logger import get_logger


logger = get_logger(__name__)

CHUNK_SIZE = 10_000


class StreamingConnection(Protocol):
    type: str


class StreamingEngineFactory(Protocol):
    def create_engine(self, conn: StreamingConnection) -> Engine: ...


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
        if connection.type in ("postgres", "postgresql"):
            conn.execute(text("SET statement_timeout = :t"), {"t": timeout_ms})
        elif connection.type == "mysql":
            conn.execute(text("SET SESSION max_execution_time = :t"), {"t": timeout_ms})
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
        return [c.lower() for c in columns]
    return columns


def _normalize_decimal(value: Any) -> Any:
    """Converte ``Decimal`` in stringa compatta evitando la coda di zeri.

    psycopg2 mappa i tipi numerici PG (``NUMERIC``/``DECIMAL``, ritornati da
    ``AVG``/``SUM`` su colonne numeric) a ``decimal.Decimal``. Senza
    normalizzazione, ``csv.writer.writerows`` chiama ``str(Decimal)`` che
    preserva la scala configurata: ``AVG(age)`` su NUMERIC(20,16) esce
    ``36.5000000000000000`` invece di ``36.5``.

    Formattazione ``%.15g``: 15 cifre significative (precisione float64),
    no notazione scientifica per numeri "normali". Coerente col fix gia'
    applicato in ``dataframe_io._compact_whole_number_floats`` + ``to_csv``.
    """
    if isinstance(value, Decimal):
        # Decimal -> formattazione compatta. Cast a float ok: 15 cifre
        # significative coprono la precisione che il consumer si aspetta
        # in CSV; le query che richiedono precisione esatta usano
        # parquet/altro o quote esplicite.
        return f"{float(value):.15g}"
    return value


def _normalize_row(row: Any) -> tuple:
    return tuple(_normalize_decimal(v) for v in row)


def _write_csv(result: Any, columns: List[str], output_path: Path, max_rows: int) -> int:
    total_rows = 0
    with open(output_path, "w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(columns)
        while True:
            chunk = result.fetchmany(CHUNK_SIZE)
            if not chunk:
                break
            writer.writerows(_normalize_row(r) for r in chunk)
            total_rows += len(chunk)
            _raise_if_over_row_cap(total_rows, max_rows)
    return total_rows


def _write_parquet(result: Any, columns: List[str], output_path: Path, max_rows: int) -> int:
    import pyarrow as pa
    import pyarrow.parquet as pq

    total_rows = 0
    pq_writer = None
    try:
        while True:
            chunk = result.fetchmany(CHUNK_SIZE)
            if not chunk:
                break
            df_chunk = pd.DataFrame(chunk, columns=columns)
            table = pa.Table.from_pandas(df_chunk, preserve_index=False)
            if pq_writer is None:
                pq_writer = pq.ParquetWriter(output_path, table.schema)
            pq_writer.write_table(table)
            total_rows += len(chunk)
            _raise_if_over_row_cap(total_rows, max_rows)
    finally:
        if pq_writer is not None:
            pq_writer.close()
    return total_rows


def execute_streaming_to_file(
    *,
    engine_factory: StreamingEngineFactory,
    connection: StreamingConnection,
    sql: str,
    params: Optional[Dict[str, Any]],
    output_path: Path,
    output_format: str,
) -> Tuple[int, List[str]]:
    """Execute a query and write CSV/Parquet output without materializing all rows."""
    output_path = Path(output_path)
    engine = engine_factory.create_engine(connection)
    max_rows = _max_streaming_rows()

    with engine.connect() as conn:
        _configure_session(conn, connection)
        result = conn.execute(text(sql), params or {})
        columns = _columns_from_result(result, connection)

        if output_format == "csv":
            total_rows = _write_csv(result, columns, output_path, max_rows)
        elif output_format == "parquet":
            total_rows = _write_parquet(result, columns, output_path, max_rows)
        else:
            raise ValueError(
                f"_execute_streaming_to_file: formato non supportato "
                f"per streaming: {output_format}. Use csv o parquet."
            )

    return total_rows, columns
