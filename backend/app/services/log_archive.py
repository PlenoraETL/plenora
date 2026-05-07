"""KEEP_COMPAT wrapper for scheduler log archive ownership.

Canonical implementation:
`app.core.scheduler.log_archive`.

This module remains as a temporary import and monkeypatch surface for
legacy callers while production code migrates to the scheduler runtime
boundary.
"""

from __future__ import annotations

from app.core.scheduler.log_archive import (
    _MAX_BATCH_SEQ,
    ArchiveResult,
    LogArchiveWriter,
    _build_object_key,
    _normalize_datetime,
    _serialize_rows,
    _table_ts_column,
)

__all__ = [
    "_MAX_BATCH_SEQ",
    "ArchiveResult",
    "LogArchiveWriter",
    "_build_object_key",
    "_normalize_datetime",
    "_serialize_rows",
    "_table_ts_column",
]
