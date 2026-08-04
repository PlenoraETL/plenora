"""Optional native XLSX accelerator with a safe Python fallback boundary.

The dataframe is serialized as UTF-8 CSV directly to the exporter stdin.  No
CSV data file is created.  The native XLSX is first written to a private
temporary directory and validated before it is copied to the caller's sink, so
an exporter failure cannot leave a partial multipart upload behind.
"""

from __future__ import annotations

import io
import json
import logging
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import zipfile
from contextlib import suppress
from pathlib import Path
from typing import Any, Mapping, TypedDict

import pandas as pd

from app.config import settings


logger = logging.getLogger(__name__)

_ENGINE_ENV = "PLENORA_XLSX_ENGINE"
_EXPORTER_PATH_ENV = "PLENORA_XLSX_EXPORTER_PATH"
_TIMEOUT_ENV = "PLENORA_XLSX_EXPORT_TIMEOUT_SECONDS"
_DEFAULT_TIMEOUT_SECONDS = 900.0
_COPY_CHUNK_BYTES = 1024 * 1024
_SUPPORTED_ENGINES = {"auto", "python", "rust"}


class NativeXlsxUnsupported(ValueError):
    """The dataframe cannot retain its XLSX semantics through the native CLI."""


def _native_xlsx_has_multiindex_columns(df: Any) -> bool:
    return isinstance(df.columns, pd.MultiIndex)


def _native_xlsx_object_column_type(series: Any) -> str:
    inferred = pd.api.types.infer_dtype(series, skipna=True)
    if inferred in {"empty", "string", "unicode", "bytes"}:
        return "string"
    if inferred == "boolean":
        return "boolean"
    if inferred == "integer":
        return "integer"
    if inferred in {"floating", "mixed-integer-float"}:
        return "float"
    if inferred == "decimal":
        return "decimal"
    if inferred == "date":
        return "date"
    if inferred in {"datetime", "datetime64", "datetime64tz"}:
        return "datetime"
    raise NativeXlsxUnsupported(
        f"tipo object XLSX non supportato dal bridge nativo: {inferred}"
    )


def _native_xlsx_column_type(series: Any, declared_type: str) -> str:
    if declared_type == "date":
        return "date"
    if declared_type in {"datetime", "timestamp"} or declared_type.startswith(
        "datetime64"
    ):
        return "datetime"

    dtype = series.dtype
    if pd.api.types.is_bool_dtype(dtype):
        return "boolean"
    if pd.api.types.is_integer_dtype(dtype):
        return "integer"
    if pd.api.types.is_float_dtype(dtype):
        return "float"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "datetime"
    if pd.api.types.is_timedelta64_dtype(dtype):
        return "string"
    if pd.api.types.is_object_dtype(dtype) or isinstance(dtype, pd.CategoricalDtype):
        return _native_xlsx_object_column_type(series)
    if pd.api.types.is_string_dtype(dtype):
        return "string"
    raise NativeXlsxUnsupported(f"dtype XLSX non supportato: {dtype}")


class _NativeColumnSchema(TypedDict):
    name: str
    type: str


class _NativeExportSchema(TypedDict):
    schema_version: int
    sheet_name: str
    columns: list[_NativeColumnSchema]
    delimiter: str
    null_token: str
    row_count_hint: int
    temp_dir: str


def _configured_engine() -> str:
    engine = str(settings.get(_ENGINE_ENV, "auto") or "auto").strip().lower()
    if engine not in _SUPPORTED_ENGINES:
        logger.warning(
            "%s=%r non valido; uso il writer XLSX Python",
            _ENGINE_ENV,
            engine,
        )
        return "python"
    return engine


def _default_exporter_path() -> Path:
    backend_root = Path(__file__).resolve().parents[3]
    filename = "plenora-xlsx-exporter.exe" if os.name == "nt" else "plenora-xlsx-exporter"
    return backend_root / "bin" / filename


def _configured_exporter_path() -> Path:
    configured = str(settings.get(_EXPORTER_PATH_ENV) or "").strip()
    return Path(configured).expanduser() if configured else _default_exporter_path()


def _configured_timeout_seconds() -> float:
    raw_value = str(
        settings.get(_TIMEOUT_ENV, _DEFAULT_TIMEOUT_SECONDS)
        or _DEFAULT_TIMEOUT_SECONDS
    ).strip()
    if not raw_value:
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw_value)
    except ValueError:
        logger.warning(
            "%s=%r non valido; uso il timeout predefinito di %.0f secondi",
            _TIMEOUT_ENV,
            raw_value,
            _DEFAULT_TIMEOUT_SECONDS,
        )
        return _DEFAULT_TIMEOUT_SECONDS
    return min(max(value, 10.0), 7_200.0)


def _declared_type_for_column(
    column: Any,
    column_types: Mapping[str, Any] | None,
) -> str:
    if not column_types:
        return ""
    name = str(column)
    direct = column_types.get(name)
    if direct is not None:
        return str(direct).strip().lower()
    lowered = name.lower()
    for candidate, raw_type in column_types.items():
        if str(candidate).lower() == lowered:
            return str(raw_type or "").strip().lower()
    return ""


def _build_schema(
    df: Any,
    *,
    sheet_name: str,
    column_types: Mapping[str, Any] | None,
    null_token: str,
    temp_dir: Path,
) -> _NativeExportSchema:
    if _native_xlsx_has_multiindex_columns(df):
        raise NativeXlsxUnsupported("le colonne MultiIndex richiedono il writer Python")
    if any(not isinstance(column, str) for column in df.columns):
        raise NativeXlsxUnsupported(
            "le intestazioni non testuali richiedono il writer Python"
        )

    columns: list[_NativeColumnSchema] = []
    for index, name in enumerate(df.columns):
        series = df.iloc[:, index]
        declared_type = _declared_type_for_column(name, column_types)
        columns.append(
            {
                "name": name,
                "type": _native_xlsx_column_type(series, declared_type),
            }
        )
    return {
        "schema_version": 1,
        "sheet_name": sheet_name,
        "columns": columns,
        "delimiter": ",",
        "null_token": null_token,
        "row_count_hint": len(df),
        "temp_dir": str(temp_dir),
    }


def _run_exporter(
    df: Any,
    *,
    exporter: Path,
    schema_path: Path,
    output_path: Path,
    null_token: str,
    timeout_seconds: float,
) -> None:
    command = [
        str(exporter),
        "export",
        "--input",
        "-",
        "--schema",
        str(schema_path),
        "--output",
        str(output_path),
    ]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creation_flags,
    )
    if process.stdin is None:
        process.kill()
        raise RuntimeError("stdin dell'exporter XLSX non disponibile")

    timed_out = threading.Event()

    def _kill_on_timeout() -> None:
        if process.poll() is None:
            timed_out.set()
            with suppress(OSError):
                process.kill()

    watchdog = threading.Timer(timeout_seconds, _kill_on_timeout)
    watchdog.daemon = True
    watchdog.start()
    write_error: Exception | None = None
    text_stdin = io.TextIOWrapper(process.stdin, encoding="utf-8", newline="")
    try:
        try:
            try:
                df.to_csv(
                    text_stdin,
                    index=False,
                    na_rep=null_token,
                    lineterminator="\n",
                )
                text_stdin.flush()
            except (BrokenPipeError, OSError, TypeError, ValueError) as exc:
                write_error = exc
            finally:
                with suppress(BrokenPipeError, OSError, ValueError):
                    text_stdin.close()
                # communicate() must not flush the already closed wrapper.
                process.stdin = None
        finally:
            # Reap the child even if dataframe serialization raises an
            # application-specific exception outside the narrow fallback set.
            stdout, stderr = process.communicate()
    finally:
        watchdog.cancel()

    if timed_out.is_set():
        raise TimeoutError(
            f"export XLSX nativo oltre il timeout di {timeout_seconds:.0f} secondi"
        )
    if write_error is not None:
        raise RuntimeError(f"errore invio dataframe all'exporter: {write_error}") from write_error
    if process.returncode != 0:
        diagnostic = stderr.decode("utf-8", errors="replace").strip()[-4_000:]
        raise RuntimeError(
            f"exporter XLSX terminato con codice {process.returncode}: {diagnostic}"
        )
    if not stdout:
        logger.debug("L'exporter XLSX non ha emesso il riepilogo JSON")


def _validate_xlsx(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 22:
        raise RuntimeError("l'exporter non ha prodotto un file XLSX")
    if not zipfile.is_zipfile(path):
        raise RuntimeError("l'exporter ha prodotto un contenitore XLSX non valido")


def try_write_dataframe_to_xlsx_stream(
    df: Any,
    sink: Any,
    *,
    sheet_name: str = "Sheet1",
    column_types: Mapping[str, Any] | None = None,
) -> bool:
    """Try the native exporter and return ``False`` for the Python fallback.

    No bytes are written to ``sink`` until the child process has succeeded and
    the resulting XLSX container has passed basic validation.
    """

    engine = _configured_engine()
    if engine == "python":
        return False

    exporter = _configured_exporter_path()
    if not exporter.is_file():
        if engine == "rust":
            logger.warning(
                "Exporter XLSX nativo non trovato in %s; uso il writer Python",
                exporter,
            )
        return False

    # pandas truncates long ``na_rep`` values in native float blocks (32-byte
    # formatter buffer). Keep the marker below that limit while retaining a
    # collision-resistant 96-bit random suffix.
    null_token = f"~PN~{secrets.token_hex(12)}"
    with tempfile.TemporaryDirectory(prefix="plenora-xlsx-native-") as temp:
        temp_dir = Path(temp)
        schema_path = temp_dir / "schema.json"
        output_path = temp_dir / "output.xlsx"
        try:
            schema = _build_schema(
                df,
                sheet_name=sheet_name,
                column_types=column_types,
                null_token=null_token,
                temp_dir=temp_dir,
            )
            schema_path.write_text(
                json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            _run_exporter(
                df,
                exporter=exporter,
                schema_path=schema_path,
                output_path=output_path,
                null_token=null_token,
                timeout_seconds=_configured_timeout_seconds(),
            )
            _validate_xlsx(output_path)
        except (
            NativeXlsxUnsupported,
            OSError,
            RuntimeError,
            TimeoutError,
            TypeError,
            ValueError,
            subprocess.SubprocessError,
            zipfile.BadZipFile,
        ) as exc:
            level = logging.WARNING if engine == "rust" else logging.INFO
            logger.log(
                level,
                "Export XLSX nativo non disponibile; uso il writer Python: %s",
                exc,
            )
            return False

        # Sink errors must propagate. Falling back after this point could append
        # a second workbook to an already partially written multipart upload.
        with output_path.open("rb") as source:
            shutil.copyfileobj(source, sink, length=_COPY_CHUNK_BYTES)
        return True


__all__ = [
    "NativeXlsxUnsupported",
    "try_write_dataframe_to_xlsx_stream",
]
