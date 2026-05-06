"""Leaf Parquet preview parser.

BE-GO-READY-RESOURCE-ENGINE-016-ARTIFACT-PREVIEW-PARSER-BOUNDARY.

Owns the pandas-bound parsing of a Parquet byte buffer into a
typed preview shape (rows + column schema + total row count).
Imported only by ``artifact_reader.py`` (the I/O orchestrator);
``import pandas`` lives **only** inside this module so the rest
of ``app/core/intermediate/`` and ``app/core/resources/`` does
not pull pandas at import time.

Boundary contract
-----------------
- Pure function: bytes in, ``ParquetPreview`` out (or
  ``ParquetPreviewParseError`` on decode/conversion failure).
  No I/O, no FastAPI, no ``app.config``, no object storage,
  no logging.
- pandas is the ONLY heavy import here. The import sits inside
  the function body to keep the *module* import edge cheap;
  the function's *runtime* edge is where pandas is unavoidable
  for the ``read_parquet`` boundary.
- The decode-error envelope (``ParquetPreviewParseError``) is
  typed and carries the ``stage`` (``"read_parquet"`` or
  ``"to_dict"``) plus the original exception so the caller can
  build the user-visible error string in its own format
  without leaking pyarrow / pandas internals.
- On ``to_dict`` failure the error carries partial schema +
  row_count so the caller can surface "rows unavailable but
  schema known" — preserving the pre-016 surface of
  ``ArtifactPreviewResult``.

Anti-god-engine note
--------------------
This module is a pure parser. It MUST NOT gain dependencies on
``app.config``, ``app.core.auth``, ``app.shared.cloud_backends``,
FastAPI, or object storage. The existing import-boundary tests
under ``tests2/intermediate/unit/`` lock this contract.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ParquetPreview:
    """Parsed Parquet preview.

    ``rows`` is ``df.head(limit).to_dict(orient='records')``.
    ``col_schema`` maps ``{col_name: dtype_str}`` for the FULL
    frame (not just the head, so the consumer learns the
    dataset's schema even when limit is small).
    ``row_count`` is the FULL row count of the parquet, NOT the
    number of returned rows.
    """

    rows: List[Dict[str, Any]] = field(default_factory=list)
    col_schema: Dict[str, str] = field(default_factory=dict)
    row_count: int = 0


class ParquetPreviewParseError(Exception):
    """Raised when the byte buffer cannot be decoded into a preview.

    ``stage`` is one of:

      - ``"read_parquet"``: ``pd.read_parquet(BytesIO(...))``
        failed; no schema or row count is available.
      - ``"to_dict"``: the parquet decoded fine but converting
        the head to a list-of-dicts failed (e.g. a column with
        an unhashable nested type). ``partial_schema`` and
        ``partial_row_count`` carry the known-good schema +
        full row count so the caller can preserve them.

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
    """Decode a Parquet byte buffer into a typed preview.

    Caller contract:
      - ``body_bytes`` is the full Parquet file in memory
        (the I/O orchestrator owns the bounded materialization
        upstream).
      - ``limit > 0`` is validated upstream; this function
        accepts any positive int and clamps via ``df.head``.

    Failure surface:
      - ``read_parquet`` failure -> ``ParquetPreviewParseError(
        stage='read_parquet', cause=<original>)`` with empty
        partial fields.
      - ``to_dict`` failure -> ``ParquetPreviewParseError(
        stage='to_dict', cause=<original>,
        partial_schema={...}, partial_row_count=N)``.

    Success surface: ``ParquetPreview(rows, col_schema,
    row_count)``.
    """
    # Lazy / leaf import: pandas lives ONLY inside this function
    # body (and transitively, ONLY inside this module). Keeping
    # the import inside the call site means a process that imports
    # ``preview_parser`` but never invokes the parser does not pay
    # the pandas import cost. More importantly, the source-bound
    # import-boundary tests can lock that NO sibling module under
    # ``app/core/intermediate/`` references pandas anymore.
    import pandas as pd

    try:
        df = pd.read_parquet(io.BytesIO(body_bytes))
    except Exception as e:
        raise ParquetPreviewParseError(
            stage="read_parquet",
            cause=e,
        ) from e

    row_count = int(len(df))
    col_schema: Dict[str, str] = {
        str(c): str(df[c].dtype) for c in df.columns
    }
    head = df.head(int(limit))
    try:
        rows = head.to_dict(orient="records")
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
