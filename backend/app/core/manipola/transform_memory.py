"""Memory headroom guard for non-join MANIPOLA transformations."""

from __future__ import annotations

import math
from typing import Iterable

from app.config import settings
from app.core.dataframes.ports import FrameLike
from app.core.resources.exceptions import MemoryLimitExceededError
from app.core.resources.memory_guard import get_current_rss_bytes


TRANSFORM_MEMORY_STAGE = "manipola_transform_memory"


def _sampled_payload_bytes(frame: FrameLike, sample_rows: int = 2_048) -> int:
    rows = int(len(frame))
    if rows <= 0:
        return 0
    bounded = min(rows, sample_rows)
    try:
        sample_bytes = int(
            frame.head(bounded).memory_usage(index=False, deep=True).sum()
        )
        return math.ceil(sample_bytes / bounded) * rows
    except Exception:
        return max(1, len(getattr(frame, "columns", []))) * 8 * rows


def guard_transform_headroom(
    frames: Iterable[FrameLike],
    *,
    node_id: str,
) -> None:
    """Reserve room for one CoW/materialized output before a transform."""
    try:
        limit = int(settings.get("WORKER_RSS_MAX_BYTES", 0) or 0)
        factor = float(settings.get("MANIPOLA_TRANSFORM_HEADROOM_FACTOR", 1.25) or 1.25)
    except (TypeError, ValueError):
        return
    rss = get_current_rss_bytes()
    if limit <= 0 or rss is None:
        return
    input_payload = sum(_sampled_payload_bytes(frame) for frame in frames)
    projected = int(rss) + math.ceil(input_payload * max(1.0, factor))
    if projected > limit:
        raise MemoryLimitExceededError(
            observed_bytes=projected,
            limit_bytes=limit,
            path_basename=f"transform_{node_id}",
            stage=TRANSFORM_MEMORY_STAGE,
        )


__all__ = ["TRANSFORM_MEMORY_STAGE", "guard_transform_headroom"]
