"""Regressione RAM dell'export XLSX verso object storage.

Confronta il payload XLSX interamente materializzato in ``BytesIO``
con il writer corrente ``constant_memory`` su uno stream non seekable. Le varianti
girano in processi spawn separati per non contaminare le misure RSS.
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


_ROWS = 30_000
_COLUMNS = 16
_SAMPLE_INTERVAL_SECONDS = 0.001
# Entrambi i path condividono ora XlsxWriter constant_memory: il delta non deve
# piu' includere decine di MiB dell'object model OpenPyXL. Rimane il payload ZIP
# materializzato dal path bytes, che il sink streaming deve evitare.
_MIN_SAVED_BYTES = 512 * 1024
_MAX_STREAMING_PEAK_RATIO = 0.60
_MAX_STREAMING_PEAK_BYTES = 8 * 1024 * 1024
_MAX_STREAM_WRITE_BYTES = 1024 * 1024


class _CountingSink:
    """Sink non seekable che replica il multipart upload senza rete."""

    def __init__(self) -> None:
        self.position = 0
        self.max_write = 0

    def write(self, data: bytes) -> int:
        size = len(data)
        self.position += size
        self.max_write = max(self.max_write, size)
        return size

    def tell(self) -> int:
        return self.position

    def flush(self) -> None:
        return None

    def seekable(self) -> bool:
        return False


@dataclass(frozen=True)
class _Measurement:
    variant: str
    baseline_rss: int
    peak_rss: int
    final_rss: int
    output_size: int
    max_write: int
    elapsed_seconds: float

    @property
    def peak_delta(self) -> int:
        return max(0, self.peak_rss - self.baseline_rss)

    @property
    def retained_delta(self) -> int:
        return max(0, self.final_rss - self.baseline_rss)


def _make_frame() -> pd.DataFrame:
    numeric_columns = _COLUMNS // 2
    frame = pd.DataFrame(
        np.arange(_ROWS * numeric_columns, dtype=np.int64).reshape(
            _ROWS,
            numeric_columns,
        ),
        columns=[f"n_{index}" for index in range(numeric_columns)],
        copy=False,
    )
    text = np.array([f"payload_{index % 1000:04d}" for index in range(_ROWS)])
    for index in range(_COLUMNS - numeric_columns):
        frame[f"s_{index}"] = text
    return frame


def _measure_child(
    variant: Literal["generic", "legacy", "streaming"],
    result_queue,
) -> None:
    import psutil

    from app.core.dataframes.export_writer import (
        dataframe_to_export_bytes,
        write_dataframe_to_xlsx_stream,
    )

    frame = _make_frame()
    process = psutil.Process()
    gc.collect()
    baseline_rss = int(process.memory_info().rss)
    peak_rss = baseline_rss
    stop_sampling = threading.Event()

    def _sample_rss() -> None:
        nonlocal peak_rss
        while not stop_sampling.wait(_SAMPLE_INTERVAL_SECONDS):
            peak_rss = max(peak_rss, int(process.memory_info().rss))

    sampler = threading.Thread(target=_sample_rss, daemon=True)
    sampler.start()
    started = time.perf_counter()
    try:
        if variant == "legacy":
            payload = dataframe_to_export_bytes(frame, "xlsx")
            output_size = len(payload)
            max_write = output_size
        else:
            sink = _CountingSink()
            if variant == "generic":
                from app.core.dataframes.export_writer import (
                    write_rows_to_xlsx_stream,
                )

                write_rows_to_xlsx_stream(
                    frame.itertuples(index=False, name=None),
                    list(frame.columns),
                    sink,
                    row_count_hint=len(frame),
                )
            else:
                write_dataframe_to_xlsx_stream(frame, sink)
            output_size = sink.position
            max_write = sink.max_write
    finally:
        elapsed = time.perf_counter() - started
        stop_sampling.set()
        sampler.join(timeout=5)

    peak_rss = max(peak_rss, int(process.memory_info().rss))
    result_queue.put(
        _Measurement(
            variant=variant,
            baseline_rss=baseline_rss,
            peak_rss=peak_rss,
            final_rss=int(process.memory_info().rss),
            output_size=output_size,
            max_write=max_write,
            elapsed_seconds=elapsed,
        )
    )


def _run_isolated(
    variant: Literal["generic", "legacy", "streaming"],
) -> _Measurement:
    context = mp.get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(
        target=_measure_child,
        args=(variant, result_queue),
        name=f"xlsx-memory-{variant}",
    )
    process.start()
    deadline = time.monotonic() + 180
    try:
        while True:
            try:
                measurement = result_queue.get(timeout=0.5)
                break
            except Empty as exc:
                if not process.is_alive():
                    raise AssertionError(
                        f"Export {variant} terminato senza risultato; "
                        f"exitcode={process.exitcode}"
                    ) from exc
                if time.monotonic() >= deadline:
                    raise AssertionError(f"Export {variant} oltre timeout") from exc
    finally:
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        result_queue.close()

    assert process.exitcode == 0
    assert isinstance(measurement, _Measurement)
    return measurement


def _mib(value: int) -> float:
    return value / (1024 * 1024)


@pytest.mark.perf_bench
def test_xlsx_streaming_avoids_full_workbook_in_memory():
    legacy = _run_isolated("legacy")
    streaming = _run_isolated("streaming")

    saved = legacy.peak_delta - streaming.peak_delta
    ratio = streaming.peak_delta / max(legacy.peak_delta, 1)
    output_ratio = streaming.output_size / max(legacy.output_size, 1)

    print(
        "\n[bench XLSX streaming] "
        f"shape={_ROWS:,}x{_COLUMNS} "
        f"legacy_peak={_mib(legacy.peak_delta):.1f}MiB "
        f"streaming_peak={_mib(streaming.peak_delta):.1f}MiB "
        f"saved={_mib(saved):.1f}MiB ratio={ratio:.2f} "
        f"legacy_retained={_mib(legacy.retained_delta):.1f}MiB "
        f"streaming_retained={_mib(streaming.retained_delta):.1f}MiB\n"
        f"  legacy_time={legacy.elapsed_seconds:.3f}s "
        f"streaming_time={streaming.elapsed_seconds:.3f}s "
        f"output={_mib(streaming.output_size):.1f}MiB "
        f"max_write={_mib(streaming.max_write):.3f}MiB"
    )

    assert 0.90 <= output_ratio <= 1.10
    assert saved >= _MIN_SAVED_BYTES, (
        f"Risparmio XLSX insufficiente: {_mib(saved):.1f} MiB"
    )
    assert ratio <= _MAX_STREAMING_PEAK_RATIO, (
        f"Il writer streaming usa troppa RAM: ratio={ratio:.2f}"
    )
    assert streaming.peak_delta <= _MAX_STREAMING_PEAK_BYTES, (
        f"Picco XLSX streaming eccessivo: {_mib(streaming.peak_delta):.1f} MiB"
    )
    assert streaming.max_write <= _MAX_STREAM_WRITE_BYTES
    assert streaming.retained_delta < legacy.retained_delta


@pytest.mark.perf_bench
def test_xlsx_dataframe_dtype_dispatch_avoids_generic_cell_dispatch():
    generic = _run_isolated("generic")
    specialized = _run_isolated("streaming")

    ratio = specialized.elapsed_seconds / max(generic.elapsed_seconds, 0.001)
    output_ratio = specialized.output_size / max(generic.output_size, 1)
    print(
        "\n[bench XLSX dtype dispatch] "
        f"shape={_ROWS:,}x{_COLUMNS} "
        f"generic={generic.elapsed_seconds:.3f}s "
        f"specialized={specialized.elapsed_seconds:.3f}s ratio={ratio:.2f}"
    )

    assert 0.99 <= output_ratio <= 1.01
    # Timing smoke, deliberately tolerant to shared-runner jitter. The strict
    # performance signal is reported above; this only catches major regressions.
    assert ratio <= 1.10
