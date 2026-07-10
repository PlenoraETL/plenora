"""Benchmark RSS del preflight cardinalita' join MANIPOLA.

Confronta in processi isolati:

- legacy: filtra tutte le colonne prima di contare le join key;
- current: proietta le sole join key prima del filtro.

Il processo separato evita che le arena numpy/pandas trattenute da un run
alterino la baseline del run successivo. Il test e' ``perf_bench``: va
eseguito manualmente e non e' una prova di correttezza funzionale.
"""

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


_ROWS = 250_000
_WIDTH = 32
_NULL_STRIDE = 20
_MIN_SAVED_BYTES = 32 * 1024 * 1024
_MAX_OPTIMIZED_TO_LEGACY_RATIO = 0.50


@dataclass(frozen=True)
class RssMeasurement:
    variant: str
    estimate: int
    baseline_rss: int
    peak_rss: int
    final_rss: int
    elapsed_seconds: float

    @property
    def peak_delta(self) -> int:
        return max(0, self.peak_rss - self.baseline_rss)


def _make_wide_join_inputs(
    rows: int,
    width: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Crea due frame larghi a blocco float contiguo.

    Il 5% circa delle chiavi e' NaN: il legacy non puo' usare il fast-path
    pandas della mask tutta True e materializza realmente tutte le colonne.
    """
    left_values = np.ones((rows, width), dtype=np.float64)
    right_values = np.ones((rows, width), dtype=np.float64)
    keys = np.arange(rows, dtype=np.float64) % 1_000
    keys[::_NULL_STRIDE] = np.nan
    left_values[:, 0] = keys
    right_values[:, 0] = keys

    left_columns = ["k", *(f"left_payload_{idx}" for idx in range(1, width))]
    right_columns = ["rk", *(f"right_payload_{idx}" for idx in range(1, width))]
    return (
        pd.DataFrame(left_values, columns=left_columns, copy=False),
        pd.DataFrame(right_values, columns=right_columns, copy=False),
    )


def _legacy_estimate(
    left: pd.DataFrame,
    right: pd.DataFrame,
) -> int:
    """Implementazione precedente al commit 7193a58e."""
    left_keys = ["k"]
    right_keys = ["rk"]
    left_non_null = left[left[left_keys].notna().all(axis=1)]
    right_non_null = right[right[right_keys].notna().all(axis=1)]

    def _key_counts(df, keys):
        grouped = df.groupby(keys[0], dropna=False).size()
        return {key: int(count) for key, count in grouped.items()}

    left_counts = _key_counts(left_non_null, left_keys)
    right_counts = _key_counts(right_non_null, right_keys)
    matched_keys = set(left_counts).intersection(right_counts)
    return sum(left_counts[key] * right_counts[key] for key in matched_keys)


def _current_estimate(
    left: pd.DataFrame,
    right: pd.DataFrame,
) -> int:
    from app.core.manipola.transforms.joins import (
        _estimate_join_output_rows_by_keys,
    )

    estimate = _estimate_join_output_rows_by_keys(
        left,
        right,
        ["k"],
        ["rk"],
        "inner",
    )
    assert estimate is not None
    return estimate


def _measure_in_child(
    variant: Literal["legacy", "optimized"],
    rows: int,
    width: int,
    result_queue,
) -> None:
    import psutil

    left, right = _make_wide_join_inputs(rows, width)
    gc.collect()

    process = psutil.Process()
    baseline_rss = int(process.memory_info().rss)
    peak_rss = baseline_rss
    stop_sampling = threading.Event()

    def _sample_rss() -> None:
        nonlocal peak_rss
        while not stop_sampling.is_set():
            peak_rss = max(peak_rss, int(process.memory_info().rss))
            time.sleep(0.001)
        peak_rss = max(peak_rss, int(process.memory_info().rss))

    sampler = threading.Thread(target=_sample_rss, daemon=True)
    sampler.start()
    started = time.perf_counter()
    try:
        estimate = (
            _legacy_estimate(left, right)
            if variant == "legacy"
            else _current_estimate(left, right)
        )
    finally:
        elapsed = time.perf_counter() - started
        stop_sampling.set()
        sampler.join(timeout=5)

    measurement = RssMeasurement(
        variant=variant,
        estimate=int(estimate),
        baseline_rss=baseline_rss,
        peak_rss=peak_rss,
        final_rss=int(process.memory_info().rss),
        elapsed_seconds=elapsed,
    )
    result_queue.put(measurement)


def _run_isolated(
    variant: Literal["legacy", "optimized"],
    *,
    rows: int = _ROWS,
    width: int = _WIDTH,
) -> RssMeasurement:
    context = mp.get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(
        target=_measure_in_child,
        args=(variant, rows, width, result_queue),
        name=f"manipola-memory-{variant}",
    )
    process.start()
    try:
        measurement = result_queue.get(timeout=120)
    except Empty as exc:
        raise AssertionError(
            f"Benchmark {variant} senza risultato; exitcode={process.exitcode}"
        ) from exc
    finally:
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        result_queue.close()

    assert process.exitcode == 0
    assert isinstance(measurement, RssMeasurement)
    return measurement


def _mib(value: int) -> float:
    return value / (1024 * 1024)


@pytest.mark.perf_bench
def test_bench_join_cardinality_key_projection_reduces_peak_rss():
    legacy = _run_isolated("legacy")
    optimized = _run_isolated("optimized")

    assert optimized.estimate == legacy.estimate

    saved_bytes = legacy.peak_delta - optimized.peak_delta
    ratio = optimized.peak_delta / max(legacy.peak_delta, 1)
    print(
        "\n[bench manipola join RSS] "
        f"shape=2x({_ROWS:,}x{_WIDTH}) "
        f"legacy_delta={_mib(legacy.peak_delta):.1f}MiB "
        f"optimized_delta={_mib(optimized.peak_delta):.1f}MiB "
        f"saved={_mib(saved_bytes):.1f}MiB "
        f"ratio={ratio:.2f} "
        f"legacy_time={legacy.elapsed_seconds:.3f}s "
        f"optimized_time={optimized.elapsed_seconds:.3f}s"
    )

    assert saved_bytes >= _MIN_SAVED_BYTES, (
        f"Risparmio RSS insufficiente: {_mib(saved_bytes):.1f} MiB < "
        f"{_mib(_MIN_SAVED_BYTES):.1f} MiB"
    )
    assert ratio <= _MAX_OPTIMIZED_TO_LEGACY_RATIO, (
        f"Picco ottimizzato troppo vicino al legacy: ratio={ratio:.2f} > "
        f"{_MAX_OPTIMIZED_TO_LEGACY_RATIO:.2f}"
    )
