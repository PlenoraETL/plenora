"""
Portable read/preview orchestration for FileSourceService.

Stream-first: `load_dataframe` e `get_layers_info` ricevono un context manager
che espone un file-like seekable. Il payload object-storage viene copiato a
chunk in uno `SpooledTemporaryFile`: resta in RAM solo sotto la soglia di spool
e passa a un file temporaneo anonimo oltre soglia, sempre con cleanup.
"""

from __future__ import annotations

import gc

from typing import Any, BinaryIO, Callable, ContextManager, Optional, Tuple
from typing_extensions import TypedDict

from app.core.dataframes import load_dispatch as load_dispatch_module
from app.core.dataframes.tabular import dataframe_preview_records
from app.core.io import geospatial as geospatial_io_module
from app.core.files.models import FileSourceLayersResponse, FileSourcePreview


AssertVisible = Callable[[str, str], None]
GetFileSource = Callable[[str], Any]
OpenBlob = Callable[[str, Optional[str]], ContextManager[BinaryIO]]


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
    open_blob: OpenBlob,
    logger: Any,
) -> Tuple[Any, Optional[Any]]:
    """Load a persisted FileSource senza una copia completa ``bytes``.

    Visibility remains fail-closed: callers inject the same
    `FileSourceService._assert_visible` callback.
    """
    assert_visible(file_id, expected_group_id or "")
    file_source = get_file_source(file_id)
    if not file_source:
        raise ValueError(f"File source '{file_id}' non trovato")

    try:
        with open_blob(file_id, expected_group_id) as blob_stream:
            use_layer = layer or file_source.layer
            use_sheet = sheet or file_source.sheet
            df, gdf = load_dispatch_module.load_by_format_stream(
                blob_stream, file_source.format,
                layer=use_layer, sheet=use_sheet,
            )
    except FileNotFoundError as exc:
        raise ValueError(f"Nessun dato trovato per '{file_id}'") from exc
    gc.collect()
    return df, gdf


def get_layers_info(
    file_id: str,
    *,
    expected_group_id: Optional[str] = None,
    assert_visible: AssertVisible,
    get_file_source: GetFileSource,
    open_blob: OpenBlob,
    logger: Any,
) -> FileSourceLayersPayload:
    """Return layer/sheet metadata da uno stream seekable bounded."""
    assert_visible(file_id, expected_group_id or "")
    file_source = get_file_source(file_id)
    if not file_source:
        raise ValueError(f"File source '{file_id}' non trovato")

    layers: list[str] = []
    sheets: list[str] = []
    if file_source.format in ["gdb", "gpkg", "xlsx"]:
        try:
            with open_blob(file_id, expected_group_id) as blob_stream:
                if file_source.format in ["gdb", "gpkg"]:
                    layers = geospatial_io_module.list_vector_layers_stream(
                        blob_stream
                    )
                elif file_source.format == "xlsx":
                    sheets = load_dispatch_module.list_excel_sheets_from_stream(
                        blob_stream
                    )
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
    "OpenBlob",
    "GetFileSource",
    "get_layers_info",
    "get_preview",
    "load_dataframe",
]
