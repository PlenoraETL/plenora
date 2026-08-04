"""Contract tests for the optional native XLSX subprocess boundary."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from decimal import Decimal
from pathlib import Path

import pandas as pd


class _CapturedStdin(io.BytesIO):
    def __init__(self) -> None:
        super().__init__()
        self.payload = b""

    def close(self) -> None:
        if not self.closed:
            self.payload = self.getvalue()
        super().close()


class _SuccessfulExporterProcess:
    def __init__(self, command: list[str], capture: dict[str, object]) -> None:
        self.command = command
        self.capture = capture
        self.stdin = _CapturedStdin()
        self.captured_stdin = self.stdin
        self.returncode: int | None = None

    def poll(self):
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9

    def communicate(self):
        output_path = Path(self.command[self.command.index("--output") + 1])
        schema_path = Path(self.command[self.command.index("--schema") + 1])
        self.capture["command"] = list(self.command)
        self.capture["csv"] = self.captured_stdin.payload.decode("utf-8")
        self.capture["schema"] = json.loads(schema_path.read_text(encoding="utf-8"))
        with zipfile.ZipFile(output_path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
        self.returncode = 0
        return b'{"status":"ok"}', b""


def _configure_native(monkeypatch, executable: Path) -> None:
    monkeypatch.setenv("PLENORA_XLSX_ENGINE", "rust")
    monkeypatch.setenv("PLENORA_XLSX_EXPORTER_PATH", str(executable))


def test_native_adapter_pipes_csv_to_stdin_and_copies_validated_xlsx(
    tmp_path,
    monkeypatch,
):
    from app.core.dataframes import native_xlsx
    from app.core.io import native_xlsx as native_xlsx_io

    executable = tmp_path / "plenora-xlsx-exporter.exe"
    executable.touch()
    _configure_native(monkeypatch, executable)
    capture: dict[str, object] = {}

    def _popen(command, **_kwargs):
        return _SuccessfulExporterProcess(command, capture)

    monkeypatch.setattr(native_xlsx_io.subprocess, "Popen", _popen)
    frame = pd.DataFrame(
        {
            "id": pd.Series([1, pd.NA], dtype="Int64"),
            "amount": [Decimal("1.2300"), None],
            "ratio": [1.5, None],
            "literal": ["=1+1", "testo"],
            "when": ["31/12/2024", None],
        }
    )
    sink = io.BytesIO()

    used_native = native_xlsx.try_write_dataframe_to_xlsx_stream(
        frame,
        sink,
        column_types={"when": "date"},
    )

    assert used_native is True
    assert zipfile.is_zipfile(io.BytesIO(sink.getvalue()))
    command = capture["command"]
    assert isinstance(command, list)
    assert command[command.index("--input") + 1] == "-"
    assert capture["csv"].startswith("id,amount,ratio,literal,when\n")
    schema = capture["schema"]
    assert isinstance(schema, dict)
    assert [column["type"] for column in schema["columns"]] == [
        "integer",
        "decimal",
        "float",
        "string",
        "date",
    ]
    rows = list(csv.reader(io.StringIO(capture["csv"])))
    assert rows[2][2] == schema["null_token"]


def test_native_failure_falls_back_before_writing_to_sink(
    tmp_path,
    monkeypatch,
):
    from app.core.io import native_xlsx as native_xlsx_io
    from app.core.dataframes.export_writer import write_dataframe_to_xlsx_stream

    executable = tmp_path / "plenora-xlsx-exporter.exe"
    executable.touch()
    _configure_native(monkeypatch, executable)

    def _fail(*_args, **_kwargs):
        raise RuntimeError("errore exporter simulato")

    monkeypatch.setattr(native_xlsx_io, "_run_exporter", _fail)
    sink = io.BytesIO()
    write_dataframe_to_xlsx_stream(pd.DataFrame({"id": [1, 2]}), sink)

    payload = sink.getvalue()
    assert payload.startswith(b"PK\x03\x04")
    assert zipfile.is_zipfile(io.BytesIO(payload))


def test_mixed_object_column_uses_python_fallback_without_starting_process(
    tmp_path,
    monkeypatch,
):
    from app.core.dataframes import native_xlsx
    from app.core.io import native_xlsx as native_xlsx_io

    executable = tmp_path / "plenora-xlsx-exporter.exe"
    executable.touch()
    _configure_native(monkeypatch, executable)

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("l'exporter non deve partire per tipi misti")

    monkeypatch.setattr(native_xlsx_io, "_run_exporter", _forbidden)
    sink = io.BytesIO()
    frame = pd.DataFrame({"mixed": [1, "uno"]})

    assert native_xlsx.try_write_dataframe_to_xlsx_stream(frame, sink) is False
    assert sink.getvalue() == b""
