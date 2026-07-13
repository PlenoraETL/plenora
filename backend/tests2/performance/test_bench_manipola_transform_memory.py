"""Calibrazione RSS degli estimator pivot/melt in processi isolati."""

from __future__ import annotations

import gc
import multiprocessing as mp
import threading
import time
from dataclasses import dataclass
from queue import Empty
from typing import Literal

import numpy as np
import pandas as pd
import pytest


@dataclass(frozen=True)
class MemoryCalibration:
    operation: str
    estimated_additional_bytes: int
    baseline_rss_bytes: int
    peak_rss_bytes: int

    @property
    def observed_additional_bytes(self) -> int:
        return max(0, self.peak_rss_bytes - self.baseline_rss_bytes)


def _measure_operation(
    operation: Literal["pivot", "melt", "spatial"],
    result_queue,
) -> None:
    import psutil

    from app.core.manipola.transform_memory import (
        estimate_melt_memory,
        estimate_pivot_memory,
    )

    if operation == "pivot":
        rows = 100_000
        frame = pd.DataFrame(
            {
                "entity": np.arange(rows, dtype=np.int64) % 20_000,
                "attribute": np.arange(rows, dtype=np.int64) % 20,
                "value": np.arange(rows, dtype=np.float64),
            }
        )
        estimate_factory = lambda baseline: estimate_pivot_memory(
            frame,
            index_columns=["entity"],
            pivot_column="attribute",
            value_column="value",
            worker_limit_bytes=10_000_000_000,
            current_rss_bytes=baseline,
        )
        operation_factory = lambda: frame.pivot_table(
            index=["entity"],
            columns="attribute",
            values="value",
            aggfunc="first",
        )
    elif operation == "melt":
        rows = 50_000
        value_columns = [f"value_{index}" for index in range(8)]
        frame = pd.DataFrame(
            {
                "id": np.arange(rows, dtype=np.int64),
                **{
                    column: np.arange(rows, dtype=np.float64)
                    for column in value_columns
                },
            }
        )
        estimate_factory = lambda baseline: estimate_melt_memory(
            frame,
            id_columns=["id"],
            value_columns=value_columns,
            worker_limit_bytes=10_000_000_000,
            current_rss_bytes=baseline,
        )
        operation_factory = lambda: frame.melt(
            id_vars=["id"],
            value_vars=value_columns,
        )
    else:
        import geopandas as gpd
        from shapely.geometry import Point, Polygon

        from app.core.manipola.transform_memory import (
            estimate_spatial_binary_memory,
        )

        frame = gpd.GeoDataFrame(
            {"id": range(5_000)},
            geometry=[Point(0.5, 0.5) for _ in range(5_000)],
            crs="EPSG:4326",
        )
        right = gpd.GeoDataFrame(
            {"zone": range(10)},
            geometry=[
                Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
                for _ in range(10)
            ],
            crs="EPSG:4326",
        )
        estimate_factory = lambda baseline: estimate_spatial_binary_memory(
            frame,
            right,
            operation="inner",
            worker_limit_bytes=10_000_000_000,
            current_rss_bytes=baseline,
        )
        operation_factory = lambda: frame.sjoin(
            right,
            how="inner",
            predicate="within",
        )

    gc.collect()
    process = psutil.Process()
    pre_estimate_rss = int(process.memory_info().rss)
    estimate = estimate_factory(pre_estimate_rss)
    gc.collect()
    baseline = int(process.memory_info().rss)
    peak = baseline
    stop = threading.Event()

    def _sample() -> None:
        nonlocal peak
        while not stop.is_set():
            peak = max(peak, int(process.memory_info().rss))
            time.sleep(0.001)

    sampler = threading.Thread(target=_sample, daemon=True)
    sampler.start()
    output = None
    try:
        output = operation_factory()
        peak = max(peak, int(process.memory_info().rss))
    finally:
        stop.set()
        sampler.join(timeout=5)
    assert output is not None and len(output) > 0
    result_queue.put(
        MemoryCalibration(
            operation=operation,
            estimated_additional_bytes=estimate.additional_allocation_bytes,
            baseline_rss_bytes=baseline,
            peak_rss_bytes=peak,
        )
    )


def _run_isolated(
    operation: Literal["pivot", "melt", "spatial"],
) -> MemoryCalibration:
    context = mp.get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(
        target=_measure_operation,
        args=(operation, result_queue),
        name=f"transform-memory-{operation}",
    )
    process.start()
    try:
        deadline = time.monotonic() + 120
        while True:
            try:
                measurement = result_queue.get(timeout=1)
                break
            except Empty as exc:
                if not process.is_alive() or time.monotonic() >= deadline:
                    raise AssertionError(
                        f"Calibrazione {operation} senza risultato; "
                        f"exit={process.exitcode}"
                    ) from exc
    finally:
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        result_queue.close()
    assert process.exitcode == 0
    return measurement


@pytest.mark.perf_bench
@pytest.mark.parametrize("operation", ["pivot", "melt", "spatial"])
def test_transform_estimate_covers_observed_peak_without_extreme_overestimate(
    operation,
):
    measurement = _run_isolated(operation)
    observed = measurement.observed_additional_bytes
    estimated = measurement.estimated_additional_bytes
    ratio = estimated / max(1, observed)
    print(
        f"\n[transform-memory] op={operation} "
        f"observed={observed / 1024 / 1024:.1f}MiB "
        f"estimated={estimated / 1024 / 1024:.1f}MiB ratio={ratio:.2f}"
    )
    assert estimated >= observed
    assert estimated <= max(observed * 8, 16 * 1024 * 1024)
