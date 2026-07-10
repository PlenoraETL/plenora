"""Stima e guard del picco memoria per i merge MANIPOLA."""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.config import settings
from app.core.dataframes.ports import FrameLike
from app.core.resources.exceptions import MemoryLimitExceededError
from app.core.resources.memory_guard import get_current_rss_bytes


JOIN_MEMORY_STAGE = "manipola_join_memory"
CROSS_JOIN_MEMORY_STAGE = "manipola_cross_join_memory"
MULTI_JOIN_MEMORY_STAGE = "manipola_multi_join_memory"

_ROW_WIDTH_SAMPLE_ROWS = 2_048
_MERGE_ALLOCATION_OVERHEAD_FACTOR = 2.0
_MIN_COLUMN_WIDTH_BYTES = 8


@dataclass(frozen=True)
class MergeMemoryEstimate:
    output_rows: int
    output_row_width_bytes: int
    output_payload_bytes: int
    additional_allocation_bytes: int
    projected_peak_bytes: int
    worker_limit_bytes: int


def _estimate_frame_row_width_bytes(
    df: FrameLike,
    *,
    sample_rows: int = _ROW_WIDTH_SAMPLE_ROWS,
) -> int:
    """Stima byte medi per riga su un sample bounded.

    ``deep=True`` include payload object/string senza scandire l'intero frame.
    Il floor per colonna evita zero-byte su frame vuoti o backend esotici.
    """
    column_count = max(1, len(getattr(df, "columns", [])))
    fallback = column_count * _MIN_COLUMN_WIDTH_BYTES
    row_count = int(len(df))
    if row_count <= 0:
        return fallback

    bounded_rows = max(1, min(row_count, int(sample_rows)))
    try:
        sample = df.head(bounded_rows)
        sample_bytes = int(
            sample.memory_usage(index=False, deep=True).sum()
        )
        return max(fallback, math.ceil(sample_bytes / bounded_rows))
    except Exception:
        return fallback


def estimate_merge_memory(
    left: FrameLike,
    right: FrameLike,
    output_rows: int,
    *,
    worker_limit_bytes: int,
    current_rss_bytes: int | None,
) -> MergeMemoryEstimate:
    """Stima conservativa del picco per un merge pandas-like.

    La larghezza somma entrambi i lati (incluse eventuali chiavi duplicate):
    e' intenzionalmente conservativa. Il fattore 2 copre output, indici,
    join-indexer e buffer temporanei osservati nel benchmark Dramatiq.
    """
    safe_output_rows = max(0, int(output_rows))
    left_width = _estimate_frame_row_width_bytes(left)
    right_width = _estimate_frame_row_width_bytes(right)
    output_row_width = left_width + right_width
    output_payload = safe_output_rows * output_row_width
    additional = math.ceil(
        output_payload * _MERGE_ALLOCATION_OVERHEAD_FACTOR
    )

    if current_rss_bytes is None:
        estimated_inputs = left_width * len(left) + right_width * len(right)
        current_or_inputs = int(estimated_inputs)
    else:
        current_or_inputs = max(0, int(current_rss_bytes))

    return MergeMemoryEstimate(
        output_rows=safe_output_rows,
        output_row_width_bytes=output_row_width,
        output_payload_bytes=output_payload,
        additional_allocation_bytes=additional,
        projected_peak_bytes=current_or_inputs + additional,
        worker_limit_bytes=max(0, int(worker_limit_bytes)),
    )


def guard_estimated_merge_memory(
    left: FrameLike,
    right: FrameLike,
    output_rows: int,
    *,
    stage: str,
    path_basename: str,
) -> MergeMemoryEstimate | None:
    """Blocca un merge se il picco stimato supera il cap RSS worker."""
    try:
        worker_limit = int(settings.get("WORKER_RSS_MAX_BYTES", 0) or 0)
    except (TypeError, ValueError):
        worker_limit = 0
    if worker_limit <= 0:
        return None

    estimate = estimate_merge_memory(
        left,
        right,
        output_rows,
        worker_limit_bytes=worker_limit,
        current_rss_bytes=get_current_rss_bytes(),
    )
    if estimate.projected_peak_bytes > worker_limit:
        raise MemoryLimitExceededError(
            observed_bytes=estimate.projected_peak_bytes,
            limit_bytes=worker_limit,
            path_basename=path_basename,
            stage=stage,
        )
    return estimate


__all__ = [
    "CROSS_JOIN_MEMORY_STAGE",
    "JOIN_MEMORY_STAGE",
    "MULTI_JOIN_MEMORY_STAGE",
    "MergeMemoryEstimate",
    "estimate_merge_memory",
    "guard_estimated_merge_memory",
]
