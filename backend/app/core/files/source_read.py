"""
Portable read/preview orchestration for FileSourceService.

Post-Phase4 refactor: bytes-first. Le funzioni `load_dataframe` /
`get_layers_info` consumano il blob direttamente in RAM via
`load_by_format_bytes` / `list_vector_layers` / `list_excel_sheets_from_bytes`,
senza piu' materializzare temp file via `temp_file_for`.

Il blob viene fornito dal caller come `get_blob` callable (firma
`(file_id, expected_group_id) -> Optional[bytes]`), tipicamente
`FileSourceService._get_blob_from_db`.
"""

from __future__ import annotations

import gc

from typing import Any, Callable, Optional, Tuple
from typing_extensions import TypedDict

from app.core.dataframes.tabular import dataframe_preview_records
from app.core.files.models import FileSourceLayersResponse, FileSourcePreview


AssertVisible = Callable[[str, str], None]
GetFileSource = Callable[[str], Any]
GetBlob = Callable[[str, Optional[str]], Optional[bytes]]


class FileSourceLayersPayload(TypedDict):
    file_id: str
    format: str
    layers: list[str]
    sheets: list[str]


class FileSourcePreviewColumnPayload(TypedDict):
    name: str
    type: str


class FileSourcePreviewPayload(TypedDict):
    file_id: str
    columns: list[FileSourcePreviewColumnPayload]
    rows: list[dict[str, object]]
    total_count: int
    preview_count: int


def load_dataframe(
    file_id: str,
    *,
    layer: Optional[str] = None,
    sheet: Optional[str] = None,
    expected_group_id: Optional[str] = None,
    assert_visible: AssertVisible,
    get_file_source: GetFileSource,
    get_blob: GetBlob,
    logger: Any,
) -> Tuple[Any, Optional[Any]]:
    """Load a persisted file source into a DataFrame (RAM-only).

    Visibility remains fail-closed: callers inject the same
    `FileSourceService._assert_visible` callback.
    """
    from app.core.dataframes.load_dispatch import load_by_format_bytes

    assert_visible(file_id, expected_group_id or "")
    file_source = get_file_source(file_id)
    if not file_source:
        raise ValueError(f"File source '{file_id}' non trovato")

    blob = get_blob(file_id, expected_group_id)
    if not blob:
        raise ValueError(f"Nessun dato trovato per '{file_id}'")

    try:
        use_layer = layer or file_source.layer
        use_sheet = sheet or file_source.sheet
        df, gdf = load_by_format_bytes(
            blob, file_source.format,
            layer=use_layer, sheet=use_sheet,
        )
        return df, gdf
    finally:
        # gc.collect() conservato: GeoDataFrame grandi su worker
        # memory-constrained beneficiano della liberazione esplicita,
        # anche se non ci sono piu' tempfile da rimuovere.
        try:
            del df, gdf
        except NameError:
            pass
        gc.collect()


def get_layers_info(
    file_id: str,
    *,
    expected_group_id: Optional[str] = None,
    assert_visible: AssertVisible,
    get_file_source: GetFileSource,
    get_blob: GetBlob,
    logger: Any,
) -> FileSourceLayersPayload:
    """Return layer/sheet metadata for a persisted file source (RAM-only)."""
    from app.core.dataframes.load_dispatch import list_excel_sheets_from_bytes
    from app.core.io.geospatial import list_vector_layers

    assert_visible(file_id, expected_group_id or "")
    file_source = get_file_source(file_id)
    if not file_source:
        raise ValueError(f"File source '{file_id}' non trovato")

    layers: list[str] = []
    sheets: list[str] = []
    if file_source.format in ["gdb", "gpkg", "xlsx"]:
        blob = get_blob(file_id, expected_group_id)
        if blob:
            try:
                if file_source.format in ["gdb", "gpkg"]:
                    layers = list_vector_layers(blob)
                elif file_source.format == "xlsx":
                    sheets = list_excel_sheets_from_bytes(blob)
            except Exception as e:
                logger.debug(
                    f"get_layers_info inspect fallito per {file_id} "
                    f"(fmt={file_source.format}): {e}"
                )

    try:
        return FileSourceLayersResponse(
            file_id=file_id,
            format=file_source.format,
            layers=layers,
            sheets=sheets,
        )
    except ImportError:
        pass
    return {
        "file_id": file_id,
        "format": file_source.format,
        "layers": layers,
        "sheets": sheets,
    }


def get_preview(
    file_id: str,
    *,
    limit: int = 100,
    expected_group_id: Optional[str] = None,
    assert_visible: AssertVisible,
    get_file_source: GetFileSource,
    load_dataframe_for_preview: Callable[
        [str, Optional[str]], Tuple[Any, Optional[Any]]
    ],
) -> FileSourcePreviewPayload:
    """Return a row preview for a persisted file source."""
    assert_visible(file_id, expected_group_id or "")
    file_source = get_file_source(file_id)
    if not file_source:
        raise ValueError(f"File source '{file_id}' non trovato")

    df, _gdf = load_dataframe_for_preview(file_id, expected_group_id)
    total_count = len(df)
    rows = dataframe_preview_records(df, limit)

    try:
        return FileSourcePreview(
            file_id=file_id,
            columns=file_source.columns,
            rows=rows,
            total_count=total_count,
            preview_count=len(rows),
        )
    except ImportError:
        pass
    return {
        "file_id": file_id,
        "columns": [
            {"name": c.name, "type": c.type}
            for c in file_source.columns
        ],
        "rows": rows,
        "total_count": total_count,
        "preview_count": len(rows),
    }


__all__ = [
    "AssertVisible",
    "GetBlob",
    "GetFileSource",
    "get_layers_info",
    "get_preview",
    "load_dataframe",
]
