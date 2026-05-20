"""Pure helper classes per file source upload/inspection."""

from __future__ import annotations

from pathlib import Path

from app.core.dataframes.schema import ColumnInfo, ColumnInfoBuilder, TypeInferrer


class FormatDetector:
    """Rileva formato file."""

    FORMAT_MAP = {
        '.csv': 'csv',
        '.xlsx': 'xlsx',
        '.xls': 'xlsx',
        '.json': 'json',
        '.geojson': 'geojson',
        '.parquet': 'parquet',
        '.gpkg': 'gpkg',
        '.shp': 'shp',
        '.gdb': 'gdb',
        '.zip': 'zip'
    }

    @classmethod
    def detect(cls, filename: str) -> str:
        """Rileva formato dall'estensione"""
        ext = Path(filename).suffix.lower()
        return cls.FORMAT_MAP.get(ext, 'unknown')

    @classmethod
    def is_geospatial(cls, format_type: str) -> bool:
        """Verifica se formato e' geospaziale"""
        return format_type in ['geojson', 'gpkg', 'shp', 'gdb']

    @classmethod
    def is_tabular(cls, format_type: str) -> bool:
        """Verifica se formato e' tabellare"""
        return format_type in ['csv', 'xlsx', 'json', 'parquet']


class FileIdGenerator:
    """Genera ID opachi per file source.

    `name` e `timestamp` restano nel contract del caller, ma non sono
    incorporati per evitare metadata disclosure via ID.
    """

    @staticmethod
    def generate(name: str, timestamp: str, uuid_part: str) -> str:
        """Genera ID univoco opaco.

        I parametri `name` e `timestamp` sono accettati per
        backward-compat con il contract del caller ma NON sono piu'
        incorporati nell'ID (erano vettori di metadata disclosure).
        """
        # Intenzionalmente ignorati: name, timestamp
        return f"file_{uuid_part}"

    @staticmethod
    def sanitize_name(name: str, max_length: int = 30) -> str:
        """Sanitizza nome per uso in path"""
        safe = "".join(c for c in name if c.isalnum() or c in (' ', '_')).strip()
        return safe.replace(' ', '_')[:max_length]


__all__ = [
    "ColumnInfo",
    "ColumnInfoBuilder",
    "FileIdGenerator",
    "FormatDetector",
    "TypeInferrer",
]
