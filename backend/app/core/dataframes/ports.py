"""Pandas-neutral dataframe engine contracts.

These protocols describe what the rest of the backend needs from a tabular or
geospatial frame engine without committing callers to pandas/geopandas types.
The current implementation still uses pandas/geopandas behind this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol


FrameLike = Any
GeoFrameLike = Any


@dataclass
class LoadedFrame:
    """Loaded frame plus optional geospatial representation."""

    df: FrameLike
    gdf: Optional[GeoFrameLike] = None
    format_type: str = ""


class LoggerProtocol(Protocol):
    """Small logger shape used by dataframe dispatch helpers."""

    def info(self, message: str) -> None: ...


class DataLoaderProtocol(Protocol):
    """File-source data loader contract, independent from pandas imports."""

    def load_csv(self, path: Path) -> FrameLike: ...
    def load_excel(self, path: Path, sheet: Optional[str] = None) -> FrameLike: ...
    def load_json(self, path: Path) -> FrameLike: ...
    def load_parquet(self, path: Path) -> FrameLike: ...
    def load_geospatial(self, path: Path, layer: Optional[str] = None) -> GeoFrameLike: ...
    def list_layers(self, path: Path) -> list[str]: ...
    def list_sheets(self, path: Path) -> list[str]: ...


class FrameIOEngine(Protocol):
    """Engine-level load dispatcher contract."""

    def load_path(
        self,
        file_path: Path,
        *,
        format_type: str,
        layer: Optional[str] = None,
        sheet: Optional[str] = None,
    ) -> LoadedFrame: ...


