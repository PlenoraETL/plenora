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
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from app.config import settings


logger = logging.getLogger(__name__)

_ENGINE_ENV = "PLENORA_XLSX_ENGINE"
_EXPORTER_PATH_ENV = "PLENORA_XLSX_EXPORTER_PATH"
_TIMEOUT_ENV = "PLENORA_XLSX_EXPORT_TIMEOUT_SECONDS"
_DEFAULT_TIMEOUT_SECONDS = 900.0
_COPY_CHUNK_BYTES = 1024 * 1024
_SUPPORTED_ENGINES = {"auto", "python", "rust"}


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


def _run_exporter(
    *,
    exporter: Path,
    schema_path: Path,
    output_path: Path,
    write_csv: Callable[[io.TextIOBase, str], None],
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
                write_csv(text_stdin, null_token)
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


def try_write_native_xlsx_stream(
    sink: Any,
    *,
    build_schema: Callable[[Path, str], Mapping[str, object]],
    write_csv: Callable[[io.TextIOBase, str], None],
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
            schema = build_schema(temp_dir, null_token)
            schema_path.write_text(
                json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            _run_exporter(
                exporter=exporter,
                schema_path=schema_path,
                output_path=output_path,
                write_csv=write_csv,
                null_token=null_token,
                timeout_seconds=_configured_timeout_seconds(),
            )
            _validate_xlsx(output_path)
        except (
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
    "try_write_native_xlsx_stream",
]
