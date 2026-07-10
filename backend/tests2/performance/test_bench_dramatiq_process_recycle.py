"""Soak benchmark RSS: worker riusato contro processo riciclato.

Il benchmark non assume che il riciclo riduca il picco del singolo task:
misura invece quanta memoria resta nel processo dopo pipeline consecutive e
quanto costa ripartire con un processo pulito.  Le soglie RSS non sono assert
di CI perche' allocator e sistema operativo rendono il dato machine-specific.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import threading
import time
from dataclasses import dataclass
from queue import Empty
from typing import Literal

import numpy as np
import pandas as pd
import pytest


_ROWS = 100_000
_WIDTH = 16
_NULL_STRIDE = 20
_REUSED_TASKS = 8
_RECYCLED_TASKS = 4
_SAMPLE_INTERVAL_SECONDS = 0.001
Workload = Literal["numeric", "object_nulls"]


@dataclass(frozen=True)
class TaskRss:
    rows_out: int
    columns_out: int
    before_rss: int
    peak_rss: int
    after_cleanup_rss: int
    elapsed_seconds: float

    @property
    def peak_delta(self) -> int:
        return max(0, self.peak_rss - self.before_rss)

    @property
    def retained_delta(self) -> int:
        return max(0, self.after_cleanup_rss - self.before_rss)


@dataclass(frozen=True)
class ProcessRun:
    baseline_rss: int
    tasks: tuple[TaskRss, ...]
    elapsed_seconds: float


def _make_join_inputs(
    rows: int,
    width: int,
    workload: Workload,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if workload == "numeric":
        keys = np.arange(rows, dtype=np.int64)
        left_values = np.ones((rows, width), dtype=np.float64)
        right_values = np.ones((rows, width), dtype=np.float64)
    else:
        keys = np.array([f"key_{idx}" for idx in range(rows)], dtype=object)
        keys[::_NULL_STRIDE] = None
        payload = np.array(
            [f"payload_{idx % 1_000:04d}" for idx in range(rows)],
            dtype=object,
        )
        left_values = np.column_stack([payload] * width)
        right_values = np.column_stack([payload] * width)

    left = pd.DataFrame(
        left_values,
        columns=[f"left_{idx}" for idx in range(width)],
    )
    right = pd.DataFrame(
        right_values,
        columns=[f"right_{idx}" for idx in range(width)],
    )
    left.insert(0, "key", keys)
    right.insert(0, "key", keys)
    return left, right


def _pipeline_body(
    _shim: object,
    *,
    rows: int,
    width: int,
    workload: Workload,
) -> dict[str, int]:
    from app.core.manipola.transforms.joins import op_join

    left, right = _make_join_inputs(rows, width, workload)
    result = op_join([left, right], left_on="key", right_on="key")
    return {"rows_out": len(result), "columns_out": len(result.columns)}


def _run_tasks_in_process(
    task_count: int,
    result_queue: object,
    *,
    rows: int = _ROWS,
    width: int = _WIDTH,
    workload: Workload = "numeric",
) -> None:
    """Child target: importa il worker una volta ed esegue N task."""
    import psutil

    from tests2.performance.test_bench_manipola_dramatiq_memory import (
        _install_dramatiq_import_shim,
    )

    _install_dramatiq_import_shim()
    from workers import dramatiq_app

    # Il benchmark deve misurare pandas, non il timeout del DB log handler
    # quando PostgreSQL locale non e' disponibile nel child spawnato.
    logging.disable(logging.CRITICAL)

    process = psutil.Process()
    baseline = int(process.memory_info().rss)
    process_started = time.perf_counter()
    measurements: list[TaskRss] = []

    for _ in range(task_count):
        before = int(process.memory_info().rss)
        peak = before
        stop_sampling = threading.Event()

        def _sample_rss() -> None:
            nonlocal peak
            while not stop_sampling.wait(_SAMPLE_INTERVAL_SECONDS):
                peak = max(peak, int(process.memory_info().rss))

        sampler = threading.Thread(target=_sample_rss, daemon=True)
        task_started = time.perf_counter()
        sampler.start()
        try:
            outcome = dramatiq_app._invoke_task_body(
                _pipeline_body,
                name="pipeline.execute",
                kwargs={"rows": rows, "width": width, "workload": workload},
            )
        finally:
            stop_sampling.set()
            sampler.join(timeout=5)

        peak = max(peak, int(process.memory_info().rss))
        after_cleanup = int(process.memory_info().rss)
        measurements.append(
            TaskRss(
                rows_out=int(outcome["rows_out"]),
                columns_out=int(outcome["columns_out"]),
                before_rss=before,
                peak_rss=peak,
                after_cleanup_rss=after_cleanup,
                elapsed_seconds=time.perf_counter() - task_started,
            )
        )

    result_queue.put(
        ProcessRun(
            baseline_rss=baseline,
            tasks=tuple(measurements),
            elapsed_seconds=time.perf_counter() - process_started,
        )
    )


def _spawn_run(task_count: int, workload: Workload) -> ProcessRun:
    ctx = mp.get_context("spawn")
    result_queue = ctx.Queue()
    process = ctx.Process(
        target=_run_tasks_in_process,
        args=(task_count, result_queue),
        kwargs={"workload": workload},
        name=f"dramatiq-memory-soak-{task_count}",
    )
    process.start()
    try:
        result = result_queue.get(timeout=120)
    except Empty:
        process.terminate()
        process.join(timeout=10)
        raise AssertionError("Benchmark child non ha prodotto misure RSS")
    process.join(timeout=120)
    if process.is_alive():
        process.terminate()
        process.join(timeout=10)
        raise AssertionError("Benchmark child non e' terminato")
    assert process.exitcode == 0
    assert isinstance(result, ProcessRun)
    return result


def _mib(value: int) -> float:
    return value / (1024 * 1024)


@pytest.mark.perf_bench
@pytest.mark.parametrize(
    ("workload", "expected_rows"),
    (
        ("numeric", _ROWS),
        ("object_nulls", _ROWS - ((_ROWS + _NULL_STRIDE - 1) // _NULL_STRIDE)),
    ),
)
def test_bench_reused_worker_vs_process_per_task_rss(
    workload: Workload,
    expected_rows: int,
):
    reused = _spawn_run(_REUSED_TASKS, workload)

    recycle_started = time.perf_counter()
    recycled = tuple(
        _spawn_run(1, workload) for _ in range(_RECYCLED_TASKS)
    )
    recycle_elapsed = time.perf_counter() - recycle_started

    expected_shape = (expected_rows, 1 + (_WIDTH * 2))
    assert {
        (task.rows_out, task.columns_out) for task in reused.tasks
    } == {expected_shape}
    assert {
        (run.tasks[0].rows_out, run.tasks[0].columns_out) for run in recycled
    } == {expected_shape}

    reused_idle = [
        task.after_cleanup_rss - reused.baseline_rss for task in reused.tasks
    ]
    reused_peak = max(task.peak_delta for task in reused.tasks)
    recycled_peak = max(run.tasks[0].peak_delta for run in recycled)
    recycled_reclaimed = [run.tasks[0].after_cleanup_rss for run in recycled]

    print(
        "\n[bench Dramatiq process recycle] "
        f"workload={workload} shape={expected_rows:,}x{expected_shape[1]} "
        f"reused_tasks={_REUSED_TASKS} recycled_tasks={_RECYCLED_TASKS}\n"
        "  reused idle delta after each task: "
        + ", ".join(f"{_mib(value):.1f}MiB" for value in reused_idle)
        + "\n"
        f"  reused peak delta={_mib(reused_peak):.1f}MiB "
        f"elapsed={reused.elapsed_seconds:.3f}s\n"
        f"  recycled peak delta={_mib(recycled_peak):.1f}MiB "
        "memory reclaimed on each process exit="
        + ", ".join(f"{_mib(value):.1f}MiB" for value in recycled_reclaimed)
        + f" elapsed={recycle_elapsed:.3f}s"
    )

    # Il benchmark deve misurare un vero soak e veri processi usa-e-getta.
    # Il verdetto sul rapporto RAM/latenza resta nei valori stampati, non in
    # una soglia fragile dipendente dall'allocator della macchina.
    assert len(reused.tasks) == _REUSED_TASKS
    assert len(recycled) == _RECYCLED_TASKS
