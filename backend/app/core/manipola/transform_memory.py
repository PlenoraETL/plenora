"""Preflight byte-aware per trasformazioni MANIPOLA ad alta cardinalita'."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from app.config import settings
from app.core.dataframes.ports import FrameLike
from app.core.resources.exceptions import MemoryLimitExceededError
from app.core.resources.memory_guard import get_current_rss_bytes
from app.utils.logger import get_logger


TRANSFORM_MEMORY_STAGE = "manipola_transform_memory"
PIVOT_MEMORY_STAGE = "manipola_pivot_memory"
MELT_MEMORY_STAGE = "manipola_melt_memory"
SPATIAL_MEMORY_STAGE = "manipola_spatial_memory"

SPECIALIZED_TRANSFORM_MEMORY_OPS = frozenset(
    {
        "pivot",
        "melt",
        "sjoin",
        "geo_clip",
        "geo_intersection",
        "geo_union",
        "geo_difference",
        "geo_symmetric_difference",
        "geo_count_points_in_polygons",
        "geo_overlay",
        "geo_nearest",
        "geo_within",
        "geo_explode",
    }
)

_ROW_WIDTH_SAMPLE_ROWS = 2_048
_CARDINALITY_SAMPLE_ROWS = 8_192
_SPATIAL_SAMPLE_ROWS = 128
_MIN_COLUMN_WIDTH_BYTES = 8

logger = get_logger(__name__)


@dataclass(frozen=True)
class TransformMemoryEstimate:
    operation: str
    input_rows: int
    input_payload_bytes: int
    output_rows: int
    output_columns: int
    output_row_width_bytes: int
    output_payload_bytes: int
    scratch_bytes: int
    additional_allocation_bytes: int
    current_rss_bytes: int
    projected_peak_bytes: int
    worker_limit_bytes: int


def configured_data_plane_memory_limit_bytes() -> int:
    """Limite effettivo piu' restrittivo fra worker e child usa-e-getta."""
    try:
        worker_limit = int(settings.get("WORKER_RSS_MAX_BYTES", 0) or 0)
    except (TypeError, ValueError):
        return 0
    if worker_limit <= 0:
        return 0

    mode = str(
        settings.get("DATA_PLANE_EXECUTION_MODE", "subprocess") or "subprocess"
    ).strip().lower()
    if mode != "subprocess":
        return worker_limit
    try:
        child_limit = int(
            settings.get("DATA_PLANE_CHILD_RSS_MAX_BYTES", worker_limit)
            or worker_limit
        )
    except (TypeError, ValueError):
        child_limit = worker_limit
    return min(worker_limit, child_limit) if child_limit > 0 else worker_limit


def _bounded_sample(frame: FrameLike, max_rows: int) -> FrameLike:
    rows = int(len(frame))
    bounded = min(rows, max(1, int(max_rows)))
    if bounded >= rows:
        return frame
    try:
        # Campione pseudo-casuale deterministico. Un passo regolare entra in
        # risonanza con chiavi periodiche (es. id % 20_000) e fa apparire ogni
        # riga unica, sovrastimando il pivot anche di due ordini di grandezza.
        positions = random.Random(rows ^ bounded).sample(range(rows), bounded)
        return frame.iloc[positions]
    except Exception:
        return frame.head(bounded)


def _geometry_native_bytes(sample: FrameLike) -> int:
    total = 0
    for column in getattr(sample, "columns", []):
        try:
            series = sample[column]
            if str(getattr(series, "dtype", "")) != "geometry":
                continue
            for geometry in series:
                if geometry is None:
                    continue
                total += len(geometry.wkb) + 64
        except Exception:
            continue
    return total


def estimate_frame_row_width_bytes(
    frame: FrameLike,
    *,
    sample_rows: int = _ROW_WIDTH_SAMPLE_ROWS,
) -> int:
    columns = list(getattr(frame, "columns", []))
    fallback = max(1, len(columns)) * _MIN_COLUMN_WIDTH_BYTES
    rows = int(len(frame))
    if rows <= 0:
        return fallback
    sample = _bounded_sample(frame, sample_rows)
    sampled_rows = max(1, int(len(sample)))
    try:
        sample_bytes = int(
            sample.memory_usage(index=False, deep=True).sum()
        ) + _geometry_native_bytes(sample)
        return max(fallback, math.ceil(sample_bytes / sampled_rows))
    except Exception:
        return fallback


def estimate_frame_payload_bytes(frame: FrameLike) -> int:
    return estimate_frame_row_width_bytes(frame) * int(len(frame))


def _estimate_column_width_bytes(frame: FrameLike, column: str) -> int:
    if column not in getattr(frame, "columns", []):
        return _MIN_COLUMN_WIDTH_BYTES
    sample = _bounded_sample(frame[[column]], _ROW_WIDTH_SAMPLE_ROWS)
    sampled_rows = max(1, int(len(sample)))
    try:
        sample_bytes = int(
            sample.memory_usage(index=False, deep=True).sum()
        ) + _geometry_native_bytes(sample)
        return max(
            _MIN_COLUMN_WIDTH_BYTES,
            math.ceil(sample_bytes / sampled_rows),
        )
    except Exception:
        return _MIN_COLUMN_WIDTH_BYTES


def _estimate_non_geometry_row_width_bytes(frame: FrameLike) -> int:
    total = 0
    for column in getattr(frame, "columns", []):
        try:
            if str(getattr(frame[column], "dtype", "")) == "geometry":
                continue
        except Exception:
            pass
        total += _estimate_column_width_bytes(frame, column)
    return max(_MIN_COLUMN_WIDTH_BYTES, total)


def _estimate_joint_cardinality(
    frame: FrameLike,
    columns: Sequence[str],
) -> int:
    rows = int(len(frame))
    if rows <= 0 or not columns:
        return 0
    sample = _bounded_sample(frame[list(columns)], _CARDINALITY_SAMPLE_ROWS)
    sampled_rows = max(1, int(len(sample)))
    try:
        counts = sample.value_counts(dropna=False, sort=False)
        observed = int(len(counts))
    except Exception:
        return rows
    if sampled_rows >= rows:
        return observed
    singletons = int((counts == 1).sum())
    doubletons = int((counts == 2).sum())
    if doubletons > 0:
        chao1 = observed + (singletons * singletons) / (2 * doubletons)
    else:
        # Nessun duplicato nel sample: la cardinalita' puo' davvero essere
        # vicina al numero di righe. Il fallback resta intenzionalmente safe.
        chao1 = observed + singletons * max(0, singletons - 1) / 2
    return min(rows, max(observed, math.ceil(chao1 * 1.15)))


def _build_estimate(
    frames: Sequence[FrameLike],
    *,
    operation: str,
    output_rows: int,
    output_columns: int,
    output_row_width_bytes: int,
    scratch_bytes: int,
    allocation_factor: float,
    worker_limit_bytes: int | None = None,
    current_rss_bytes: int | None = None,
) -> TransformMemoryEstimate:
    input_payload = sum(estimate_frame_payload_bytes(frame) for frame in frames)
    safe_output_rows = max(0, int(output_rows))
    safe_output_width = max(0, int(output_row_width_bytes))
    output_payload = safe_output_rows * safe_output_width
    additional = max(0, int(scratch_bytes)) + math.ceil(
        output_payload * max(1.0, float(allocation_factor))
    )
    if current_rss_bytes is None:
        sampled_rss = get_current_rss_bytes()
        current = input_payload if sampled_rss is None else max(0, int(sampled_rss))
    else:
        current = max(0, int(current_rss_bytes))
    limit = (
        configured_data_plane_memory_limit_bytes()
        if worker_limit_bytes is None
        else max(0, int(worker_limit_bytes))
    )
    return TransformMemoryEstimate(
        operation=operation,
        input_rows=sum(int(len(frame)) for frame in frames),
        input_payload_bytes=input_payload,
        output_rows=safe_output_rows,
        output_columns=max(0, int(output_columns)),
        output_row_width_bytes=safe_output_width,
        output_payload_bytes=output_payload,
        scratch_bytes=max(0, int(scratch_bytes)),
        additional_allocation_bytes=additional,
        current_rss_bytes=current,
        projected_peak_bytes=current + additional,
        worker_limit_bytes=limit,
    )


def _guard_estimate(
    estimate: TransformMemoryEstimate,
    *,
    stage: str,
    path_basename: str,
) -> TransformMemoryEstimate:
    logger.info(
        "[TRANSFORM-MEMORY] op=%s input_rows=%s input=%s B output=%s rows "
        "x %s cols x %s B payload=%s B scratch=%s B additional=%s B "
        "rss=%s B projected=%s B limit=%s B",
        estimate.operation,
        estimate.input_rows,
        estimate.input_payload_bytes,
        estimate.output_rows,
        estimate.output_columns,
        estimate.output_row_width_bytes,
        estimate.output_payload_bytes,
        estimate.scratch_bytes,
        estimate.additional_allocation_bytes,
        estimate.current_rss_bytes,
        estimate.projected_peak_bytes,
        estimate.worker_limit_bytes,
    )
    if (
        estimate.worker_limit_bytes > 0
        and estimate.projected_peak_bytes > estimate.worker_limit_bytes
    ):
        raise MemoryLimitExceededError(
            observed_bytes=estimate.projected_peak_bytes,
            limit_bytes=estimate.worker_limit_bytes,
            path_basename=path_basename,
            stage=stage,
        )
    return estimate


def estimate_pivot_memory(
    frame: FrameLike,
    *,
    index_columns: Sequence[str],
    pivot_column: str,
    value_column: str,
    mapping: Mapping[object, object] | None = None,
    worker_limit_bytes: int | None = None,
    current_rss_bytes: int | None = None,
) -> TransformMemoryEstimate:
    index_cardinality = _estimate_joint_cardinality(frame, index_columns)
    pivot_cardinality = _estimate_joint_cardinality(frame, [pivot_column])
    if mapping:
        pivot_cardinality = min(pivot_cardinality, len(mapping))
    index_width = sum(
        _estimate_column_width_bytes(frame, column) for column in index_columns
    )
    value_width = _estimate_column_width_bytes(frame, value_column)
    output_width = index_width + pivot_cardinality * value_width
    projection_width = index_width + value_width + _estimate_column_width_bytes(
        frame, pivot_column
    )
    scratch = projection_width * int(len(frame))
    return _build_estimate(
        [frame],
        operation="pivot",
        output_rows=index_cardinality,
        output_columns=len(index_columns) + pivot_cardinality,
        output_row_width_bytes=output_width,
        scratch_bytes=scratch,
        allocation_factor=2.0,
        worker_limit_bytes=worker_limit_bytes,
        current_rss_bytes=current_rss_bytes,
    )


def guard_pivot_memory(frame: FrameLike, **kwargs: object) -> TransformMemoryEstimate:
    estimate = estimate_pivot_memory(frame, **kwargs)
    return _guard_estimate(
        estimate,
        stage=PIVOT_MEMORY_STAGE,
        path_basename="pivot_output",
    )


def estimate_melt_memory(
    frame: FrameLike,
    *,
    id_columns: Sequence[str],
    value_columns: Sequence[str],
    worker_limit_bytes: int | None = None,
    current_rss_bytes: int | None = None,
) -> TransformMemoryEstimate:
    value_columns = list(value_columns)
    output_rows = int(len(frame)) * len(value_columns)
    id_width = sum(
        _estimate_column_width_bytes(frame, column) for column in id_columns
    )
    value_width = max(
        (_estimate_column_width_bytes(frame, column) for column in value_columns),
        default=_MIN_COLUMN_WIDTH_BYTES,
    )
    variable_width = max(
        _MIN_COLUMN_WIDTH_BYTES,
        math.ceil(
            sum(len(str(column).encode("utf-8")) + 49 for column in value_columns)
            / max(1, len(value_columns))
        ),
    )
    output_width = id_width + variable_width + value_width
    return _build_estimate(
        [frame],
        operation="melt",
        output_rows=output_rows,
        output_columns=len(id_columns) + 2,
        output_row_width_bytes=output_width,
        scratch_bytes=math.ceil(estimate_frame_payload_bytes(frame) * 0.25),
        allocation_factor=1.75,
        worker_limit_bytes=worker_limit_bytes,
        current_rss_bytes=current_rss_bytes,
    )


def guard_melt_memory(frame: FrameLike, **kwargs: object) -> TransformMemoryEstimate:
    estimate = estimate_melt_memory(frame, **kwargs)
    return _guard_estimate(
        estimate,
        stage=MELT_MEMORY_STAGE,
        path_basename="melt_output",
    )


def _estimate_spatial_candidate_rows(left: FrameLike, right: FrameLike) -> int:
    left_rows = int(len(left))
    right_rows = int(len(right))
    if left_rows <= 0 or right_rows <= 0:
        return 0
    sample = _bounded_sample(left, _SPATIAL_SAMPLE_ROWS)
    sampled_rows = max(1, int(len(sample)))
    try:
        query_result = right.sindex.query(sample.geometry)
        shape = getattr(query_result, "shape", ())
        candidate_count = (
            int(shape[1])
            if len(shape) == 2
            else int(len(query_result))
        )
    except Exception:
        # Senza spatial index non esiste una stima affidabile: usa il
        # worst-case cartesiano e lascia al guard la decisione fail-safe.
        return left_rows * right_rows
    if sampled_rows >= left_rows:
        return min(left_rows * right_rows, candidate_count)
    margin = max(3, math.ceil(math.sqrt(candidate_count + 1)))
    extrapolated = math.ceil(
        left_rows * ((candidate_count + margin) / sampled_rows) * 1.25
    )
    return min(left_rows * right_rows, extrapolated)


def estimate_spatial_binary_memory(
    left: FrameLike,
    right: FrameLike,
    *,
    operation: str,
    worker_limit_bytes: int | None = None,
    current_rss_bytes: int | None = None,
) -> TransformMemoryEstimate:
    left_rows = int(len(left))
    right_rows = int(len(right))
    candidates = _estimate_spatial_candidate_rows(left, right)
    normalized = str(operation or "intersection").lower()
    if normalized in {"union", "symmetric_difference"}:
        output_rows = left_rows + right_rows + candidates
    elif normalized == "right":
        output_rows = max(right_rows, candidates)
    elif normalized in {"left", "clip", "difference", "identity"}:
        output_rows = max(left_rows, candidates)
    else:
        output_rows = candidates
    left_width = estimate_frame_row_width_bytes(left)
    right_width = estimate_frame_row_width_bytes(right)
    if normalized in {"inner", "left", "sjoin"}:
        # GeoPandas sjoin mantiene la geometria sinistra e soltanto gli
        # attributi destri (non duplica la geometria destra nell'output).
        output_width = left_width + _estimate_non_geometry_row_width_bytes(right)
    elif normalized == "right":
        output_width = right_width + _estimate_non_geometry_row_width_bytes(left)
    else:
        output_width = left_width + right_width
        output_width = math.ceil(output_width * 1.5)
    input_payload = estimate_frame_payload_bytes(left) + estimate_frame_payload_bytes(right)
    return _build_estimate(
        [left, right],
        operation=f"spatial_{normalized}",
        output_rows=output_rows,
        output_columns=len(set(left.columns) | set(right.columns)),
        output_row_width_bytes=output_width,
        scratch_bytes=math.ceil(input_payload * 0.75),
        allocation_factor=2.25,
        worker_limit_bytes=worker_limit_bytes,
        current_rss_bytes=current_rss_bytes,
    )


def guard_spatial_binary_memory(
    left: FrameLike,
    right: FrameLike,
    *,
    operation: str,
    path_basename: str,
) -> TransformMemoryEstimate:
    estimate = estimate_spatial_binary_memory(
        left,
        right,
        operation=operation,
    )
    return _guard_estimate(
        estimate,
        stage=SPATIAL_MEMORY_STAGE,
        path_basename=path_basename,
    )


def estimate_spatial_explode_memory(
    frame: FrameLike,
    *,
    worker_limit_bytes: int | None = None,
    current_rss_bytes: int | None = None,
) -> TransformMemoryEstimate:
    rows = int(len(frame))
    parts = 0
    try:
        # Conteggio esatto e O(1) extra-memory: un singolo MultiPolygon con
        # migliaia di parti potrebbe sfuggire a qualunque campione bounded.
        for geometry in frame.geometry:
            if geometry is None:
                parts += 1
                continue
            geometries = getattr(geometry, "geoms", None)
            parts += max(1, len(geometries) if geometries is not None else 1)
    except Exception:
        parts = rows
    output_rows = parts
    input_payload = estimate_frame_payload_bytes(frame)
    return _build_estimate(
        [frame],
        operation="spatial_explode",
        output_rows=output_rows,
        output_columns=len(getattr(frame, "columns", [])),
        output_row_width_bytes=estimate_frame_row_width_bytes(frame),
        scratch_bytes=math.ceil(input_payload * 0.5),
        allocation_factor=2.0,
        worker_limit_bytes=worker_limit_bytes,
        current_rss_bytes=current_rss_bytes,
    )


def guard_spatial_explode_memory(frame: FrameLike) -> TransformMemoryEstimate:
    return _guard_estimate(
        estimate_spatial_explode_memory(frame),
        stage=SPATIAL_MEMORY_STAGE,
        path_basename="spatial_explode_output",
    )


def guard_transform_headroom(
    frames: Iterable[FrameLike],
    *,
    node_id: str,
) -> None:
    """Fallback per trasformazioni prive di un estimator specializzato."""
    frame_list = list(frames)
    limit = configured_data_plane_memory_limit_bytes()
    if limit <= 0 or not frame_list:
        return
    try:
        factor = float(
            settings.get("MANIPOLA_TRANSFORM_HEADROOM_FACTOR", 1.25) or 1.25
        )
    except (TypeError, ValueError):
        factor = 1.25
    input_payload = sum(estimate_frame_payload_bytes(frame) for frame in frame_list)
    rss = get_current_rss_bytes()
    current = input_payload if rss is None else max(0, int(rss))
    projected = current + math.ceil(input_payload * max(1.0, factor))
    if projected > limit:
        raise MemoryLimitExceededError(
            observed_bytes=projected,
            limit_bytes=limit,
            path_basename=f"transform_{node_id}",
            stage=TRANSFORM_MEMORY_STAGE,
        )


__all__ = [
    "MELT_MEMORY_STAGE",
    "PIVOT_MEMORY_STAGE",
    "SPATIAL_MEMORY_STAGE",
    "SPECIALIZED_TRANSFORM_MEMORY_OPS",
    "TRANSFORM_MEMORY_STAGE",
    "TransformMemoryEstimate",
    "configured_data_plane_memory_limit_bytes",
    "estimate_frame_payload_bytes",
    "estimate_frame_row_width_bytes",
    "estimate_melt_memory",
    "estimate_pivot_memory",
    "estimate_spatial_binary_memory",
    "estimate_spatial_explode_memory",
    "guard_melt_memory",
    "guard_pivot_memory",
    "guard_spatial_binary_memory",
    "guard_spatial_explode_memory",
    "guard_transform_headroom",
]
