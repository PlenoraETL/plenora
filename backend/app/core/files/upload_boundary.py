"""HTTP upload boundary helpers for file source endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional
from typing_extensions import TypedDict

from fastapi import HTTPException, UploadFile

from app.utils.mime_validator import MimeMismatchError, validate_no_executable, validate_upload


FORBIDDEN_BASENAMES = {"", ".", ".."}
BINARY_UPLOAD_EXTENSIONS = {".zip", ".gz", ".tar", ".7z", ".rar", ".dxf"}
PASSTHROUGH_PREVIEW_EXTENSIONS = {"zip", "gz", "tar", "7z", "rar", "dxf"}
ALLOWED_UPLOAD_EXTENSIONS = {
    "csv",
    "xlsx",
    "xls",
    "json",
    "geojson",
    "gpkg",
    "shp",
    "gdb",
    "zip",
    "dxf",
}
ALLOWED_MULTI_LAYER_EXTENSIONS = {"gpkg", "gdb", "zip", "xlsx", "xls"}


class PassthroughPreviewPayload(TypedDict):
    columns: list[object]
    rows: list[object]
    total_count: int
    preview_count: int
    format: str
    message: str


def sanitize_upload_filename(raw: Optional[str]) -> str:
    """Return a basename-only filename safe for storage."""
    if not raw:
        raise HTTPException(status_code=400, detail="Nome file non valido")
    name = Path(raw.replace("\\", "/")).name
    if name in FORBIDDEN_BASENAMES:
        raise HTTPException(status_code=400, detail="Nome file non valido")
    if "\x00" in name:
        raise HTTPException(
            status_code=400,
            detail="Nome file contiene caratteri non validi",
        )
    name = name.strip().lstrip("/\\")
    if not name or name in FORBIDDEN_BASENAMES:
        raise HTTPException(status_code=400, detail="Nome file non valido")
    return name


# MIME magic bytes vivono nei primi N byte
# dell'header (PNG sig 8B, ZIP "PK" 2B, ELF "\x7fELF" 4B, MZ "MZ" 2B,
# Mach-O fat headers <16B, ecc.). 4KB e' largamente sufficiente per
# `filetype`/`validate_no_executable` senza materializzare il body
# completo.
_MIME_HEADER_BYTES = 4096


def read_upload_content_for_validation(file: UploadFile, *, n: int = _MIME_HEADER_BYTES) -> bytes:
    """Read upload HEADER for MIME validation and rewind the file object.

    Legge solo `n` byte (default 4KB), sufficiente per
    `filetype`/`validate_no_executable` su tutti i magic bytes noti.
    Il `file.file.seek(0)` finale rimette il puntatore all'inizio
    perche' il caller spesso legge il body successivamente (es.
    preview_file_temp). Il nome resta per backwards-compat dei
    chiamanti esistenti.
    """
    file.file.seek(0)
    content = file.file.read(n)
    file.file.seek(0)
    return content


def validate_upload_content(
    file: UploadFile,
    *,
    filename: str,
    allowed_extensions: set[str],
    logger: Any,
    username: str,
    context: str,
) -> None:
    """Run content-based upload MIME validation and raise HTTP 400 on mismatch."""
    content = read_upload_content_for_validation(file)
    try:
        validate_upload(
            content=content,
            filename=filename,
            allowed_extensions=allowed_extensions,
        )
    except MimeMismatchError as exc:
        logger.warning(
            f"{context} rifiutato (MIME): filename={filename!r} "
            f"user={username}: {exc}"
        )
        raise HTTPException(status_code=400, detail=str(exc))


def validate_preview_content(
    file: UploadFile,
    *,
    logger: Any,
    username: str,
) -> None:
    """Block executable payloads in temporary preview uploads."""
    content = read_upload_content_for_validation(file)
    try:
        validate_no_executable(
            content=content,
            filename=file.filename or "upload",
        )
    except MimeMismatchError as exc:
        logger.warning(
            f"Preview rifiutata (MIME): filename={file.filename!r} "
            f"user={username}: {exc}"
        )
        raise HTTPException(status_code=400, detail=str(exc))


def parse_selected_columns(raw: Optional[str], *, logger: Any) -> Optional[Any]:
    """Parse selected_columns form JSON, preserving the previous lenient policy."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"Errore parsing selected_columns: {raw}")
        return None


def parse_optional_int(raw: Optional[str], *, logger: Any, label: str) -> Optional[int]:
    """Parse optional integer form values, preserving the previous lenient policy."""
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"{label} non valido: {raw}")
        return None


def parse_layers_config(raw: str) -> list[Any]:
    """Parse and validate upload-multi-layer layer configuration."""
    try:
        layers_config = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="layers_config non e' JSON valido")

    if not isinstance(layers_config, list) or len(layers_config) == 0:
        raise HTTPException(
            status_code=400,
            detail="layers_config deve essere un array non vuoto",
        )
    if len(layers_config) > 256:
        raise HTTPException(
            status_code=400,
            detail="layers_config supera il massimo di 256 elementi",
        )

    seen_sources: set[tuple[str, str]] = set()
    for index, item in enumerate(layers_config):
        if not isinstance(item, dict):
            raise HTTPException(
                status_code=400,
                detail=f"layers_config[{index}] deve essere un oggetto",
            )
        layer = item.get("layer")
        sheet = item.get("sheet")
        if bool(layer) == bool(sheet):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"layers_config[{index}] deve specificare esattamente "
                    "uno tra layer e sheet"
                ),
            )
        source_kind = "layer" if layer else "sheet"
        source_name = layer if layer else sheet
        if not isinstance(source_name, str) or not source_name.strip():
            raise HTTPException(
                status_code=400,
                detail=f"layers_config[{index}].{source_kind} non valido",
            )
        source_key = (source_kind, source_name.strip())
        if source_key in seen_sources:
            raise HTTPException(
                status_code=400,
                detail=f"layers_config contiene {source_kind} duplicato: {source_name}",
            )
        seen_sources.add(source_key)

        selected_columns = item.get("selected_columns")
        if selected_columns is not None and (
            not isinstance(selected_columns, list)
            or len(selected_columns) == 0
            or not all(
                isinstance(column, str) and bool(column.strip())
                for column in selected_columns
            )
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"layers_config[{index}].selected_columns deve essere "
                    "null oppure un array non vuoto di stringhe non vuote"
                ),
            )
    return layers_config


def is_binary_upload(filename: str) -> bool:
    """Return whether a filename should use the binary/raw upload path."""
    return any(filename.lower().endswith(ext) for ext in BINARY_UPLOAD_EXTENSIONS)


def passthrough_preview_response(
    filename: Optional[str],
) -> Optional[PassthroughPreviewPayload]:
    """Return the raw-file preview fallback response when the extension is allowed."""
    if not filename or "." not in filename:
        return None
    file_ext = filename.rsplit(".", 1)[-1].lower()
    if file_ext not in PASSTHROUGH_PREVIEW_EXTENSIONS:
        return None
    return {
        "columns": [],
        "rows": [],
        "total_count": 0,
        "preview_count": 0,
        "format": file_ext,
        "message": (
            f"File .{file_ext} non parsabile come tabulare - "
            "utilizzabile con Eseguibile Locale"
        ),
    }
