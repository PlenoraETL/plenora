"""
Leaf Parquet preview parser.

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ParquetPreview:
    """Parsed Parquet preview.

    ``rows`` is at most ``limit`` rows materialized via
    ``RecordBatch.to_pandas.to_dict(orient='records')``.
    ``col_schema`` maps ``{col_name: dtype_str}`` for the FULL
    frame schema (derived from the parquet footer's arrow
    schema, not just the first batch — so a wide-but-empty file
    still surfaces every declared column). ``row_count`` is the
    FULL row count of the parquet (read from
    ``ParquetFile.metadata.num_rows``), NOT the number of
    returned rows.
    """

    rows: List[Dict[str, Any]] = field(default_factory=list)
    col_schema: Dict[str, str] = field(default_factory=dict)
    row_count: int = 0


class ParquetPreviewParseError(Exception):
    """Raised when the byte buffer cannot be decoded into a preview.

    ``stage`` is one of:

      - ``"read_parquet"``: opening the parquet file, reading
        its footer, deriving the schema, or decoding the first
        ; no schema or row count is available
        (partial fields are empty).
      - ``"to_dict"``: the parquet decoded fine but converting
        the head a list-of-dicts failed (e.g. a
        column with an unhashable nested type).
        ``partial_schema`` and ``partial_row_count`` carry the
        known-good schema + full row count so the caller can
        preserve them.

    ``cause`` is the original exception (kept for logging /
    error envelope construction).
    """

    def __init__(
        self,
        *,
        stage: str,
        cause: BaseException,
        partial_schema: Optional[Dict[str, str]] = None,
        partial_row_count: int = 0,
    ):
        self.stage = stage
        self.cause = cause
        self.partial_schema: Dict[str, str] = partial_schema or {}
        self.partial_row_count = partial_row_count
        super().__init__(
            f"{stage}: {type(cause).__name__}: {cause}"
        )


def parse_parquet_preview(
    body_bytes: bytes,
    *,
    limit: int,
) -> ParquetPreview:
    """Decode a Parquet byte buffer into a typed preview using
    pyarrow row-group streaming (
    018).

    Caller contract:
      - ``body_bytes`` is the full Parquet file in memory
        (the I/O orchestrator owns the bounded materialization
        upstream).
      - ``limit > 0`` is validated upstream; this function
        accepts any positive int and stops reading batches
        once the cumulative row count meets it.

    Failure surface:
      - ``read_parquet`` failure (file open / footer parse /
        schema derive / ) ->
        ``ParquetPreviewParseError(stage='read_parquet',
        cause=<original>)`` with empty partial fields.
      - ``to_dict`` failure -> ``ParquetPreviewParseError(
        stage='to_dict', cause=<original>,
        partial_schema={...}, partial_row_count=N)``.

    Success surface: ``ParquetPreview(rows, col_schema,
    row_count)``.
    """
    # Lazy / leaf imports: pyarrow + pandas live ONLY inside
    # this function body (and transitively, ONLY inside this
    # module). Keeping the imports inside the call site means
    # a process that imports ``preview_parser`` but never
    # invokes the parser does not pay the import cost. The
    # sibling module under ``app/core/intermediate/`` references
    # pyarrow/pandas anymore
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    try:
        # ``pa.BufferReader`` is a zero-copy seekable view over
        # ``body_bytes``. ``ParquetFile`` parses only the file
        # footer here (a few KB at the tail of the file
        # regardless of the file's data size
        reader = pa.BufferReader(body_bytes)
        pf = pq.ParquetFile(reader)
        row_count = int(pf.metadata.num_rows)
        # Schema derivation uses an empty pandas DataFrame from the
        # arrow schema, so dtype strings match ``pd.read_parquet`` on a
        # fully decoded frame. Footer schema also preserves columns
        # when a wide file's first row-group happens to be empty.
        empty_df = pd.DataFrame(pf.schema_arrow.empty_table().to_pandas())
        col_schema: Dict[str, str] = {
            str(c): str(empty_df[c].dtype) for c in empty_df.columns
        }
    except Exception as e:
        raise ParquetPreviewParseError(
            stage="read_parquet",
            cause=e,
        ) from e

    # Stage 2: stream first batch(es) up to ``limit`` rows
    try:
        target = int(limit)
        collected: List[pa.RecordBatch] = []
        gathered = 0
        # ``iter_batches(batch_size=target)`` is the row-group
        # streaming primitive. pyarrow may emit batches smaller
        # than ``batch_size`` when a row-group's tail is hit
        # we therefore loop until we have ``>= target`` rows or
        # the file is exhausted. Critically, the loop ``break``s
        # on the first satisfaction — subsequent row-groups are
        # never read, so files with N >> limit rows pay only
        # the first row-group's decode cost
        for batch in pf.iter_batches(batch_size=target):
            collected.append(batch)
            gathered += batch.num_rows
            if gathered >= target:
                break

        if collected:
            head_table = pa.Table.from_batches(
                collected, schema=pf.schema_arrow,
            )
            head_df = pd.DataFrame(head_table.to_pandas()).head(target)
        else:
            # Zero-row file: head_df is the same empty-schema
            # frame we already built for col_schema
            head_df = empty_df
    except Exception as e:
        raise ParquetPreviewParseError(
            stage="read_parquet",
            cause=e,
        ) from e

    try:
        rows = head_df.to_dict(orient="records")
    except Exception as e:
        raise ParquetPreviewParseError(
            stage="to_dict",
            cause=e,
            partial_schema=col_schema,
            partial_row_count=row_count,
        ) from e

    return ParquetPreview(
        rows=rows,
        col_schema=col_schema,
        row_count=row_count,
    )


__all__ = [
    "ParquetPreview",
    "ParquetPreviewParseError",
    "parse_parquet_preview",
]
