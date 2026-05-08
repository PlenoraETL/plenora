"""
Parser dispatch for file sources.

The implementation is owned by `app.core.files`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Protocol, Tuple

import pandas as pd


class DataLoaderProtocol(Protocol):
    def load_csv(self, file_path: Path) -> pd.DataFrame:
        ...

    def load_excel(self, file_path: Path, sheet: Optional[str]) -> pd.DataFrame:
        ...

    def load_json(self, file_path: Path) -> pd.DataFrame:
        ...

    def load_geospatial(
        self, file_path: Path, layer: Optional[str]
    ) -> pd.DataFrame:
        ...


class LoggerProtocol(Protocol):
    def info(self, message: str) -> None:
        ...


_GEOSPATIAL_FORMATS = ("geojson", "gpkg", "shp", "gdb")


def load_by_format(
    loader: DataLoaderProtocol,
    file_path: Path,
    format_type: str,
    layer: Optional[str] = None,
    sheet: Optional[str] = None,
    *,
    logger: LoggerProtocol,
) -> Tuple[pd.DataFrame, Optional[pd.DataFrame]]:
    """Load data through the loader method selected by file format."""
    gdf: Optional[pd.DataFrame] = None

    if format_type == "csv":
        df = loader.load_csv(file_path)
    elif format_type == "xlsx":
        df = loader.load_excel(file_path, sheet)
    elif format_type == "json":
        df = loader.load_json(file_path)
    elif format_type in _GEOSPATIAL_FORMATS:
        gdf = loader.load_geospatial(file_path, layer)
        df = pd.DataFrame(gdf)
        if "geometry" in df.columns:
            df["geometry"] = gdf.geometry.apply(lambda g: g.wkt if g else None)
    else:
        raise ValueError(f"Formato '{format_type}' non supportato")

    logger.info(f"Caricato: {len(df)} righe, {len(df.columns)} colonne")
    return df, gdf
