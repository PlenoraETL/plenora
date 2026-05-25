"""DataFrame parser dispatch by source format."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

from app.core.dataframes.ports import (
    DataLoaderProtocol,
    FrameIOEngine,
    LoadedFrame,
    LoggerProtocol,
)


_GEOSPATIAL_FORMATS = ("geojson", "gpkg", "shp", "gdb")
_ZIP_LIKE_FORMATS = ("zip",)


def load_by_format_bytes(
    data: bytes,
    format_type: str,
    layer: Optional[str] = None,
    sheet: Optional[str] = None,
) -> Tuple[pd.DataFrame, Optional[pd.DataFrame]]:
    """RAM-only dispatch da bytes — non passa per filesystem.

    Equivalente bytes-first di `load_by_format`: usa
    `_load_dataframe_from_bytes` (frame_io) per i formati tabulari +
    `read_vector_bytes` (io.geospatial) per i formati vector. Per ZIP
    delega a `_load_dataframe_from_bytes(..., format_hint="zip")` che
    a sua volta usa pyogrio /vsizip per shapefile/gdb e
    zipfile+dispatch per single-file zippato.

    Ritorna `(df, gdf)`: gdf e' un GeoDataFrame quando il formato e'
    spaziale, altrimenti None. df e' sempre un DataFrame pandas (per
    i geospatial e' la versione tabular del gdf con geometry serial-
    izzata via geometry_series_to_wkt).
    """
    from app.core.dataframes.frame_io import _load_dataframe_from_bytes

    gdf: Optional[pd.DataFrame] = None

    if format_type in _GEOSPATIAL_FORMATS or format_type in _ZIP_LIKE_FORMATS:
        # _load_dataframe_from_bytes per gpkg/shp/zip/gdb passa via
        # pyogrio (vector bytes nativi) e ritorna GeoDataFrame.
        gdf = _load_dataframe_from_bytes(
            data, format_hint=format_type, sheet=sheet, layer=layer,
        )
        df = pd.DataFrame(gdf)
        if "geometry" in df.columns:
            from app.core.dataframes.geometry_serialization import (
                geometry_series_to_wkt,
            )
            df["geometry"] = geometry_series_to_wkt(gdf.geometry)
        return df, gdf

    df = _load_dataframe_from_bytes(
        data, format_hint=format_type, sheet=sheet, layer=layer,
    )
    return df, None


def list_excel_sheets_from_bytes(data: bytes) -> list[str]:
    """List worksheet names in an XLSX file from bytes (no filesystem)."""
    from openpyxl import load_workbook
    wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def load_frame_by_format(
    loader: DataLoaderProtocol,
    file_path: Path,
    format_type: str,
    layer: Optional[str] = None,
    sheet: Optional[str] = None,
    *,
    logger: LoggerProtocol,
) -> LoadedFrame:
    """Load a DataFrame through the loader method selected by file format."""
    gdf: Optional[pd.DataFrame] = None

    if format_type == "csv":
        df = loader.load_csv(file_path)
    elif format_type == "xlsx":
        df = loader.load_excel(file_path, sheet)
    elif format_type == "json":
        df = loader.load_json(file_path)
    elif format_type == "parquet":
        df = loader.load_parquet(file_path)
    elif format_type in _GEOSPATIAL_FORMATS:
        gdf = loader.load_geospatial(file_path, layer)
        df = pd.DataFrame(gdf)
        if "geometry" in df.columns:
            # Pre-fix: `apply(lambda g: g.wkt if g else None)` aveva
            # due bug latenti — (1) `if g` su una geometria empty
            # (es. POLYGON EMPTY) e' falsy in alcune versioni Shapely
            # → empty geom mappato a None invece che a "POLYGON EMPTY",
            # silente perdita; (2) per-riga su grandi GeoSeries (5k+
            # geom). Post-fix: serializzazione vettoriale via
            # `geometry_series_to_wkt`, contratto esplicito su missing
            # = None, empty preservato a meno di `empty_as_null=True`.
            from app.core.dataframes.geometry_serialization import (
                geometry_series_to_wkt,
            )
            df["geometry"] = geometry_series_to_wkt(gdf.geometry)
    else:
        raise ValueError(f"Formato '{format_type}' non supportato")

    logger.info(f"Caricato: {len(df)} righe, {len(df.columns)} colonne")
    return LoadedFrame(df=df, gdf=gdf, format_type=format_type)


def load_by_format(
    loader: DataLoaderProtocol,
    file_path: Path,
    format_type: str,
    layer: Optional[str] = None,
    sheet: Optional[str] = None,
    *,
    logger: LoggerProtocol,
) -> Tuple[pd.DataFrame, Optional[pd.DataFrame]]:
    """Compatibility tuple wrapper around `load_frame_by_format`."""
    loaded = load_frame_by_format(
        loader,
        file_path,
        format_type,
        layer,
        sheet,
        logger=logger,
    )
    return loaded.df, loaded.gdf


__all__ = [
    "DataLoaderProtocol",
    "FrameIOEngine",
    "LoadedFrame",
    "LoggerProtocol",
    "list_excel_sheets_from_bytes",
    "load_by_format",
    "load_by_format_bytes",
    "load_frame_by_format",
]
