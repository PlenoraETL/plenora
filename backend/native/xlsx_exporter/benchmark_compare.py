"""Manual end-to-end benchmark for the Python and native XLSX exporters.

The native timing deliberately includes CSV serialization and process startup,
because those costs are part of the production adapter path.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa


BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.dataframes.export_writer import write_dataframe_to_xlsx_stream  # noqa: E402


def _make_frame(rows: int, columns: int) -> pd.DataFrame:
    numeric_columns = columns // 2
    frame = pd.DataFrame(
        np.arange(rows * numeric_columns, dtype=np.int64).reshape(
            rows,
            numeric_columns,
        ),
        columns=[f"n_{index}" for index in range(numeric_columns)],
        copy=False,
    )
    text = np.array([f"payload_{index % 1000:04d}" for index in range(rows)])
    for index in range(columns - numeric_columns):
        frame[f"s_{index}"] = text
    return frame


def _schema(frame: pd.DataFrame, null_token: str) -> dict[str, object]:
    columns = [
        {
            "name": str(name),
            "type": "integer" if pd.api.types.is_integer_dtype(dtype) else "string",
        }
        for name, dtype in zip(frame.columns, frame.dtypes, strict=True)
    ]
    return {
        "schema_version": 1,
        "sheet_name": "Sheet1",
        "columns": columns,
        "delimiter": ",",
        "null_token": null_token,
        "row_count_hint": len(frame),
    }


def _measure(
    frame: pd.DataFrame,
    exporter: Path,
    workdir: Path,
) -> tuple[float, float, float, float, float, int]:
    python_output = workdir / "python.xlsx"
    native_output = workdir / "native.xlsx"
    csv_input = workdir / "input.csv"
    schema_input = workdir / "schema.json"
    null_token = "__PLENORA_BENCH_NULL_8f417c9d__"

    started = time.perf_counter()
    with python_output.open("wb") as sink:
        write_dataframe_to_xlsx_stream(frame, sink)
    python_seconds = time.perf_counter() - started

    started = time.perf_counter()
    frame.to_csv(csv_input, index=False, na_rep=null_token)
    schema_input.write_text(
        json.dumps(_schema(frame, null_token)),
        encoding="utf-8",
    )
    bridge_seconds = time.perf_counter() - started

    started = time.perf_counter()
    subprocess.run(
        [
            str(exporter),
            "export",
            "--input",
            str(csv_input),
            "--schema",
            str(schema_input),
            "--output",
            str(native_output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    exporter_seconds = time.perf_counter() - started

    arrow_output = workdir / "input.arrow"
    started = time.perf_counter()
    table = pa.Table.from_pandas(frame, preserve_index=False)
    with pa.OSFile(str(arrow_output), "wb") as sink:
        with pa.ipc.new_file(sink, table.schema) as writer:
            writer.write_table(table)
    arrow_bridge_seconds = time.perf_counter() - started

    pipe_output = workdir / "native-pipe.xlsx"
    previous_engine = os.environ.get("PLENORA_XLSX_ENGINE")
    previous_exporter = os.environ.get("PLENORA_XLSX_EXPORTER_PATH")
    os.environ["PLENORA_XLSX_ENGINE"] = "rust"
    os.environ["PLENORA_XLSX_EXPORTER_PATH"] = str(exporter)
    try:
        started = time.perf_counter()
        with pipe_output.open("wb") as sink:
            write_dataframe_to_xlsx_stream(frame, sink)
        pipe_seconds = time.perf_counter() - started
    finally:
        if previous_engine is None:
            os.environ.pop("PLENORA_XLSX_ENGINE", None)
        else:
            os.environ["PLENORA_XLSX_ENGINE"] = previous_engine
        if previous_exporter is None:
            os.environ.pop("PLENORA_XLSX_EXPORTER_PATH", None)
        else:
            os.environ["PLENORA_XLSX_EXPORTER_PATH"] = previous_exporter

    if not zipfile.is_zipfile(native_output):
        raise RuntimeError("L'exporter nativo non ha prodotto un XLSX valido")
    return (
        python_seconds,
        bridge_seconds,
        exporter_seconds,
        arrow_bridge_seconds,
        pipe_seconds,
        native_output.stat().st_size,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exporter", required=True, type=Path)
    parser.add_argument("--rows", nargs="+", type=int, default=[30_000, 100_000, 500_000])
    parser.add_argument("--columns", type=int, default=16)
    parser.add_argument("--rounds", type=int, default=1)
    args = parser.parse_args()

    if not args.exporter.is_file():
        parser.error(f"exporter non trovato: {args.exporter}")

    print(
        "rows,columns,python_s,csv_bridge_s,arrow_bridge_s,exporter_s,"
        "csv_e2e_s,pipe_e2e_s,arrow_e2e_est_s,engine_speedup,"
        "csv_e2e_speedup,pipe_e2e_speedup,arrow_e2e_est_speedup,native_mib"
    )
    with tempfile.TemporaryDirectory(prefix="plenora-xlsx-bench-") as temp:
        root = Path(temp)
        for rows in args.rows:
            frame = _make_frame(rows, args.columns)
            python_samples: list[float] = []
            bridge_samples: list[float] = []
            exporter_samples: list[float] = []
            arrow_bridge_samples: list[float] = []
            pipe_samples: list[float] = []
            native_size = 0
            for round_index in range(args.rounds):
                run_dir = root / f"{rows}-{round_index}"
                run_dir.mkdir()
                (
                    python_time,
                    bridge_time,
                    exporter_time,
                    arrow_bridge_time,
                    pipe_time,
                    native_size,
                ) = _measure(
                    frame,
                    args.exporter,
                    run_dir,
                )
                python_samples.append(python_time)
                bridge_samples.append(bridge_time)
                exporter_samples.append(exporter_time)
                arrow_bridge_samples.append(arrow_bridge_time)
                pipe_samples.append(pipe_time)
            python_median = statistics.median(python_samples)
            bridge_median = statistics.median(bridge_samples)
            exporter_median = statistics.median(exporter_samples)
            arrow_bridge_median = statistics.median(arrow_bridge_samples)
            pipe_median = statistics.median(pipe_samples)
            native_median = bridge_median + exporter_median
            arrow_native_estimate = arrow_bridge_median + exporter_median
            print(
                f"{rows},{args.columns},{python_median:.3f},{bridge_median:.3f},"
                f"{arrow_bridge_median:.3f},{exporter_median:.3f},"
                f"{native_median:.3f},{pipe_median:.3f},"
                f"{arrow_native_estimate:.3f},"
                f"{python_median / exporter_median:.2f}x,"
                f"{python_median / native_median:.2f}x,"
                f"{python_median / pipe_median:.2f}x,"
                f"{python_median / arrow_native_estimate:.2f}x,"
                f"{native_size / 1024 / 1024:.2f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
