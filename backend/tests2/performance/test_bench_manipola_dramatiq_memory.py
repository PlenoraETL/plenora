"""Benchmark end-to-end simulato del worker Dramatiq MANIPOLA.

Il test esegue un join reale dentro ``workers.dramatiq_app._invoke_task_body``
e misura RSS per fase: source loading, cardinality preflight, merge, ritorno e
cleanup worker. Legacy e current girano in processi spawn separati.
"""

from __future__ import annotations

import gc
import multiprocessing as mp
import sys
import threading
import time
from dataclasses import dataclass
from queue import Empty
from types import ModuleType
from typing import Literal

import numpy as np
import pandas as pd
import pytest


_ROWS = 150_000
_WIDTH = 24
_NULL_STRIDE = 20
_SAMPLE_INTERVAL_SECONDS = 0.001
_MIN_PREFLIGHT_SAVED_BYTES = 24 * 1024 * 1024
_MAX_PREFLIGHT_RATIO = 0.60
_MAX_TOTAL_PEAK_REGRESSION_RATIO = 1.05


@dataclass(frozen=True)
class WorkerMemoryMeasurement:
    variant: str
    rows_out: int
    columns_out: int
    baseline_rss: int
    peak_rss: int
    final_rss: int
    elapsed_seconds: float
    phase_peaks: tuple[tuple[str, int], ...]

    @property
    def peak_delta(self) -> int:
        return max(0, self.peak_rss - self.baseline_rss)

    @property
    def retained_delta(self) -> int:
        return max(0, self.final_rss - self.baseline_rss)

    def phase_delta(self, phase: str) -> int:
        peaks = dict(self.phase_peaks)
        return max(0, peaks.get(phase, self.baseline_rss) - self.baseline_rss)


def _make_join_inputs(
    rows: int,
    width: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    left_values = np.ones((rows, width), dtype=np.float64)
    right_values = np.ones((rows, width), dtype=np.float64)
    keys = np.arange(rows, dtype=np.float64)
    keys[::_NULL_STRIDE] = np.nan
    left_values[:, 0] = keys
    right_values[:, 0] = keys

    left_columns = ["k", *(f"left_payload_{idx}" for idx in range(1, width))]
    right_columns = ["rk", *(f"right_payload_{idx}" for idx in range(1, width))]
    return (
        pd.DataFrame(left_values, columns=left_columns, copy=False),
        pd.DataFrame(right_values, columns=right_columns, copy=False),
    )


def _legacy_estimate_join_rows(
    left: pd.DataFrame,
    right: pd.DataFrame,
    left_keys: list[str],
    right_keys: list[str],
    how: str,
) -> int | None:
    """Cardinality preflight precedente alla proiezione key-only."""
    if how != "inner":
        raise AssertionError("Il benchmark legacy supporta solo inner join")
    left_non_null = left[left[left_keys].notna().all(axis=1)]
    right_non_null = right[right[right_keys].notna().all(axis=1)]

    def _key_counts(df, keys):
        grouped = df.groupby(keys[0], dropna=False).size()
        return {key: int(count) for key, count in grouped.items()}

    left_counts = _key_counts(left_non_null, left_keys)
    right_counts = _key_counts(right_non_null, right_keys)
    matched_keys = set(left_counts).intersection(right_counts)
    return sum(left_counts[key] * right_counts[key] for key in matched_keys)


def _install_dramatiq_import_shim() -> None:
    """Installa il minimo import surface quando dramatiq non e' presente.

    Il benchmark usa direttamente il wrapper reale del worker, non broker o
    actor. Lo shim replica quindi solo i simboli necessari a importare
    ``workers.dramatiq_app`` nell'ambiente test ridotto.
    """
    try:
        __import__("dramatiq")
        return
    except ModuleNotFoundError:
        pass

    class _Middleware:
        pass

    class _CurrentMessage:
        @classmethod
        def get_current_message(cls):
            return None

    class _RabbitmqBroker:
        def __init__(self, **_kwargs):
            self.middleware = []

        def add_middleware(self, middleware) -> None:
            self.middleware.append(middleware)

    class _Actor:
        def __init__(self, fn, options):
            self.fn = fn
            self.actor_name = options.get("actor_name", fn.__name__)
            self.queue_name = options.get("queue_name")

        def __call__(self, *args, **kwargs):
            return self.fn(*args, **kwargs)

    dramatiq_module = ModuleType("dramatiq")
    brokers_module = ModuleType("dramatiq.brokers")
    rabbitmq_module = ModuleType("dramatiq.brokers.rabbitmq")
    middleware_module = ModuleType("dramatiq.middleware")

    def _actor(**options):
        return lambda fn: _Actor(fn, options)

    dramatiq_module.actor = _actor
    dramatiq_module.set_broker = lambda _broker: None
    rabbitmq_module.RabbitmqBroker = _RabbitmqBroker
    middleware_module.CurrentMessage = _CurrentMessage
    middleware_module.Middleware = _Middleware

    sys.modules["dramatiq"] = dramatiq_module
    sys.modules["dramatiq.brokers"] = brokers_module
    sys.modules["dramatiq.brokers.rabbitmq"] = rabbitmq_module
    sys.modules["dramatiq.middleware"] = middleware_module


def _measure_worker_child(
    variant: Literal["legacy", "optimized"],
    rows: int,
    width: int,
    result_queue,
) -> None:
    import psutil

    from app.core.manipola.transforms import joins as joins_module

    _install_dramatiq_import_shim()
    from workers import dramatiq_app

    process = psutil.Process()
    gc.collect()
    baseline_rss = int(process.memory_info().rss)
    peak_rss = baseline_rss
    current_phase = ["worker_idle"]
    phase_peaks = {"worker_idle": baseline_rss}
    stop_sampling = threading.Event()

    def _sample_rss() -> None:
        nonlocal peak_rss
        while not stop_sampling.is_set():
            rss = int(process.memory_info().rss)
            peak_rss = max(peak_rss, rss)
            phase = current_phase[0]
            phase_peaks[phase] = max(phase_peaks.get(phase, baseline_rss), rss)
            time.sleep(_SAMPLE_INTERVAL_SECONDS)
        rss = int(process.memory_info().rss)
        peak_rss = max(peak_rss, rss)
        phase = current_phase[0]
        phase_peaks[phase] = max(phase_peaks.get(phase, baseline_rss), rss)

    real_estimator = joins_module._estimate_join_output_rows_by_keys
    real_merge = joins_module.merge_dataframes
    real_cleanup = dramatiq_app._cleanup_worker_memory_after_task
    real_log_info = joins_module.logger.info
    real_log_warning = joins_module.logger.warning

    def _measured_estimator(left, right, left_keys, right_keys, how):
        current_phase[0] = "join_preflight"
        if variant == "legacy":
            return _legacy_estimate_join_rows(
                left,
                right,
                left_keys,
                right_keys,
                how,
            )
        return real_estimator(left, right, left_keys, right_keys, how)

    def _measured_merge(left, right, **kwargs):
        current_phase[0] = "join_merge"
        return real_merge(left, right, **kwargs)

    def _measured_cleanup(name: str) -> None:
        current_phase[0] = "worker_cleanup"
        real_cleanup(name)

    joins_module._estimate_join_output_rows_by_keys = _measured_estimator
    joins_module.merge_dataframes = _measured_merge
    dramatiq_app._cleanup_worker_memory_after_task = _measured_cleanup
    # Il logger applicativo puo' tentare persistenza PostgreSQL sui warning.
    # Il benchmark deve restare locale e misurare il data path, non timeout DB.
    joins_module.logger.info = lambda *_args, **_kwargs: None
    joins_module.logger.warning = lambda *_args, **_kwargs: None

    def _synthetic_pipeline_task(_self) -> dict[str, int]:
        current_phase[0] = "source_loading"
        left, right = _make_join_inputs(rows, width)
        joined = joins_module.op_join(
            [left, right],
            left_on="k",
            right_on="rk",
            how="inner",
        )
        current_phase[0] = "task_result"
        return {"rows_out": len(joined), "columns_out": len(joined.columns)}

    sampler = threading.Thread(target=_sample_rss, daemon=True)
    sampler.start()
    started = time.perf_counter()
    try:
        outcome = dramatiq_app._invoke_task_body(
            _synthetic_pipeline_task,
            name="pipeline.execute",
            kwargs={},
        )
    finally:
        elapsed = time.perf_counter() - started
        joins_module._estimate_join_output_rows_by_keys = real_estimator
        joins_module.merge_dataframes = real_merge
        dramatiq_app._cleanup_worker_memory_after_task = real_cleanup
        joins_module.logger.info = real_log_info
        joins_module.logger.warning = real_log_warning

    current_phase[0] = "worker_idle_after"
    time.sleep(0.05)
    stop_sampling.set()
    sampler.join(timeout=5)

    measurement = WorkerMemoryMeasurement(
        variant=variant,
        rows_out=int(outcome["rows_out"]),
        columns_out=int(outcome["columns_out"]),
        baseline_rss=baseline_rss,
        peak_rss=peak_rss,
        final_rss=int(process.memory_info().rss),
        elapsed_seconds=elapsed,
        phase_peaks=tuple(sorted(phase_peaks.items())),
    )
    result_queue.put(measurement)


def _run_worker_isolated(
    variant: Literal["legacy", "optimized"],
) -> WorkerMemoryMeasurement:
    context = mp.get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(
        target=_measure_worker_child,
        args=(variant, _ROWS, _WIDTH, result_queue),
        name=f"manipola-dramatiq-{variant}",
    )
    process.start()
    deadline = time.monotonic() + 120
    try:
        while True:
            try:
                measurement = result_queue.get(timeout=0.5)
                break
            except Empty as exc:
                if not process.is_alive():
                    raise AssertionError(
                        f"Worker simulato {variant} terminato senza risultato; "
                        f"exitcode={process.exitcode}"
                    ) from exc
                if time.monotonic() >= deadline:
                    raise AssertionError(
                        f"Worker simulato {variant} oltre timeout; "
                        f"exitcode={process.exitcode}"
                    ) from exc
    finally:
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        result_queue.close()

    assert process.exitcode == 0
    assert isinstance(measurement, WorkerMemoryMeasurement)
    return measurement


def _mib(value: int) -> float:
    return value / (1024 * 1024)


def _format_phases(measurement: WorkerMemoryMeasurement) -> str:
    return ", ".join(
        f"{phase}={_mib(max(0, rss - measurement.baseline_rss)):.1f}MiB"
        for phase, rss in measurement.phase_peaks
    )


@pytest.mark.perf_bench
def test_bench_simulated_dramatiq_pipeline_reduces_preflight_rss():
    legacy = _run_worker_isolated("legacy")
    optimized = _run_worker_isolated("optimized")

    assert (optimized.rows_out, optimized.columns_out) == (
        legacy.rows_out,
        legacy.columns_out,
    )

    legacy_preflight = legacy.phase_delta("join_preflight")
    optimized_preflight = optimized.phase_delta("join_preflight")
    preflight_saved = legacy_preflight - optimized_preflight
    preflight_ratio = optimized_preflight / max(legacy_preflight, 1)
    total_ratio = optimized.peak_delta / max(legacy.peak_delta, 1)

    print(
        "\n[bench simulated Dramatiq MANIPOLA] "
        f"shape=2x({_ROWS:,}x{_WIDTH}) rows_out={optimized.rows_out:,} "
        f"legacy_peak={_mib(legacy.peak_delta):.1f}MiB "
        f"optimized_peak={_mib(optimized.peak_delta):.1f}MiB "
        f"total_ratio={total_ratio:.2f} "
        f"legacy_retained={_mib(legacy.retained_delta):.1f}MiB "
        f"optimized_retained={_mib(optimized.retained_delta):.1f}MiB\n"
        f"  legacy_time={legacy.elapsed_seconds:.3f}s "
        f"optimized_time={optimized.elapsed_seconds:.3f}s\n"
        f"  legacy phases: {_format_phases(legacy)}\n"
        f"  optimized phases: {_format_phases(optimized)}"
    )

    assert preflight_saved >= _MIN_PREFLIGHT_SAVED_BYTES, (
        f"Risparmio preflight insufficiente: {_mib(preflight_saved):.1f} MiB"
    )
    assert preflight_ratio <= _MAX_PREFLIGHT_RATIO, (
        f"Preflight ottimizzato troppo vicino al legacy: {preflight_ratio:.2f}"
    )
    assert total_ratio <= _MAX_TOTAL_PEAK_REGRESSION_RATIO, (
        f"Picco task regredito: ratio={total_ratio:.2f} > "
        f"{_MAX_TOTAL_PEAK_REGRESSION_RATIO:.2f}"
    )
