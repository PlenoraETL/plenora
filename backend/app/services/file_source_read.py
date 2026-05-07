"""
portable read/preview orchestration for FileSourceService.

This module keeps DB/security-bound read workflows out of the large
"""

from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import pandas as pd


AssertVisible = Callable[[str, str], None]
GetFileSource = Callable[[str], Any]
TempFileFor = Callable[[str, Optional[str]], Optional[Path]]
LoadByFormat = Callable[[Path, str, Optional[str], Optional[str]], Tuple[pd.DataFrame, Optional[Any]]]
ListLayers = Callable[[Path], list[str]]
ListSheets = Callable[[Path], list[str]]
def _cleanup_temp_path(tmp_path: Path, format_type: str, *, logger: Any) -> None:
    from app.shared.file_cleanup import rmtree_safe

    if format_type in ("shp", "gdb"):
        try:
            rmtree_safe(tmp_path.parent, logger=logger)
        except Exception as e:
            logger.debug(
                f"cleanup load_dataframe tmp_dir fallito ({tmp_path.parent}): {e}"
            )
    else:
        try:
            rmtree_safe(tmp_path, logger=logger)
        except Exception as e:
            logger.debug(
                f"cleanup load_dataframe tmp fallito ({tmp_path}): {e}"
            )


def load_dataframe(
    file_id: str,
    *,
    layer: Optional[str] = None,
    sheet: Optional[str] = None,
    expected_group_id: Optional[str] = None,
    assert_visible: AssertVisible,
    get_file_source: GetFileSource,
    temp_file_for: TempFileFor,
    load_by_format: LoadByFormat,
    logger: Any,
) -> Tuple[pd.DataFrame, Optional[Any]]:
    """Load a persisted file source into a DataFrame.

    Visibility remains fail-closed: callers inject the same
    FileSourceService._assert_visible callback used before separation.
    """
    assert_visible(file_id, expected_group_id or "")
    file_source = get_file_source(file_id)
    if not file_source:
        raise ValueError(f"File source '{file_id}' non trovato")
    tmp_path = temp_file_for(file_id, expected_group_id)
    if not tmp_path:
        raise ValueError(f"Nessun dato trovato per '{file_id}'")

    try:
        use_layer = layer or file_source.layer
        use_sheet = sheet or file_source.sheet
        df, gdf = load_by_format(tmp_path, file_source.format, use_layer, use_sheet)
        return df, gdf
    finally:
        import gc

        try:
            del df, gdf
        except NameError:
            pass
        gc.collect()
        _cleanup_temp_path(tmp_path, file_source.format, logger=logger)


def get_layers_info(
    file_id: str,
    *,
    expected_group_id: Optional[str] = None,
    assert_visible: AssertVisible,
    get_file_source: GetFileSource,
    temp_file_for: TempFileFor,
    list_layers: ListLayers,
    list_sheets: ListSheets,
    logger: Any,
) -> Dict[str, Any]:
    """Return layer/sheet metadata for a persisted file source."""
    assert_visible(file_id, expected_group_id or "")
    file_source = get_file_source(file_id)
    if not file_source:
        raise ValueError(f"File source '{file_id}' non trovato")

    layers = []
    sheets = []
    if file_source.format in ["gdb", "gpkg", "xlsx"]:
        tmp_path = temp_file_for(file_id, expected_group_id)
        if tmp_path:
            try:
                if file_source.format in ["gdb", "gpkg"]:
                    layers = list_layers(tmp_path)
                elif file_source.format == "xlsx":
                    sheets = list_sheets(tmp_path)
            finally:
                cleanup_format = "gdb" if file_source.format == "gdb" else file_source.format
                _cleanup_temp_path(tmp_path, cleanup_format, logger=logger)

    try:
        from app.models import FileSourceLayersResponse

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
        [str, Optional[str]], Tuple[pd.DataFrame, Optional[Any]]
    ],
) -> Dict[str, Any]:
    """Return a row preview for a persisted file source."""
    assert_visible(file_id, expected_group_id or "")
    file_source = get_file_source(file_id)
    if not file_source:
        raise ValueError(f"File source '{file_id}' non trovato")

    df, _gdf = load_dataframe_for_preview(file_id, expected_group_id)
    total_count = len(df)
    preview_df = df.head(limit)
    rows = preview_df.to_dict(orient="records")

    import numpy as np

    for row in rows:
        for key, value in row.items():
            if pd.isna(value):
                row[key] = None
            elif isinstance(value, (np.integer, np.floating)):
                row[key] = value.item()
            elif isinstance(value, np.bool_):
                row[key] = bool(value)

    try:
        from app.models import FileSourcePreview

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
