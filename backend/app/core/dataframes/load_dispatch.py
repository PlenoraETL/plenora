"""DataFrame parser dispatch by source format."""

from __future__ import annotations

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
    "load_by_format",
    "load_frame_by_format",
]
