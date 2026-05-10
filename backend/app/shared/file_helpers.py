"""Pure helper classes per file source upload/inspection."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd


# Logger stdlib: file_helpers e' un leaf module e non deve dipendere da
# `app.utils.logger`. I 4 helper sono pure functions; il logger e' usato
# solo per il debug-log non critico in `ColumnInfoBuilder._extract_geometry_columns`
_logger = logging.getLogger(__name__)


# ColumnInfo: modello condiviso canonico, con fallback dataclass per test
# standalone che importano il modulo senza l'app completa.
try:
    from app.core.projects.canvas_models import ColumnInfo as _AppColumnInfo
    _USE_APP_MODELS = True
except ImportError:  # pragma: no cover
    _USE_APP_MODELS = False

if _USE_APP_MODELS:
    ColumnInfo = _AppColumnInfo
else:
    @dataclass
    class ColumnInfo:  # type: ignore[no-redef
        """Informazioni su una colonna (fallback dataclass)."""
        name: str
        type: str


class FormatDetector:
    """Rileva formato file."""

    FORMAT_MAP = {
        '.csv': 'csv',
        '.xlsx': 'xlsx',
        '.xls': 'xlsx',
        '.json': 'json',
        '.geojson': 'geojson',
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
        return format_type in ['csv', 'xlsx', 'json']


class TypeInferrer:
    """Inferisce tipi colonne."""

    WKT_PREFIXES = ('POINT', 'LINESTRING', 'POLYGON', 'MULTIPOINT',
                    'MULTILINESTRING', 'MULTIPOLYGON', 'GEOMETRYCOLLECTION')

    GEOMETRY_TYPE_MAP = {
        'point': 'geometry:point',
        'multipoint': 'geometry:multipoint',
        'linestring': 'geometry:line',
        'multilinestring': 'geometry:multiline',
        'polygon': 'geometry:polygon',
        'multipolygon': 'geometry:multipolygon'
    }

    @classmethod
    def infer_column_type(cls, series: pd.Series) -> str:
        """Inferisce tipo colonna da pandas Series"""
        dtype = str(series.dtype)

        if 'int' in dtype:
            return 'int'
        elif 'float' in dtype:
            return 'real'
        elif 'bool' in dtype:
            return 'bool'
        elif 'datetime' in dtype:
            return 'date'
        elif 'geometry' in dtype.lower():
            return 'geometry'
        else:
            return cls._infer_from_values(series)

    # Explicit date formats avoid ambiguous dateutil fallbacks and pandas
    # warnings. US month-first parsing is intentionally excluded because it
    # conflicts with Italian day-first uploads such as "03/04/2025".
    _DATE_FORMATS = (
        "%Y-%m-%d %H:%M:%S",       # ISO con time
        "%Y-%m-%dT%H:%M:%S",       # ISO8601 strict
        "%Y-%m-%d",                # ISO date only
        "%d/%m/%Y %H:%M:%S",       # IT con time
        "%d/%m/%Y",                # IT date only
        "%d-%m-%Y",                # IT con dashes
    )
    _DATE_PARSE_THRESHOLD = 0.8    # almeno 80% del sample deve matchare

    @classmethod
    def _looks_like_date_column(cls, series: pd.Series) -> bool:
        """Type detection conservativa per colonne data.

        Prova i formati di `_DATE_FORMATS` in cascata sul sample (10 valori
        non-null). Classifica `date` solo se almeno `_DATE_PARSE_THRESHOLD`
        del sample matcha un formato esplicito. Non emette warning pandas
        (no fallback dateutil), non modifica i dati: e' solo type detection
        per popolare il metadata della colonna al momento dell'upload.

        Decisione di design: meglio classificare come `str` che come
        `date` sbagliata. `errors='coerce'` su un format che non matcha
        produce NaT senza eccezione: la soglia decide. Date ambigue
        (es. `"03/04/2025"`) vengono interpretate come `dd/mm/yyyy`
        (italiano) deterministicamente. Date US-style con day > 12
        (es. `"03/15/2025"`) NON matchano nessun format -> resta `str`,
        evitando interpretazione errata.
        """
        sample = series.dropna().head(10)
        if sample.empty:
            return False
        n = len(sample)
        threshold = max(1, int(n * cls._DATE_PARSE_THRESHOLD))
        for fmt in cls._DATE_FORMATS:
            parsed = pd.to_datetime(sample, format=fmt, errors='coerce')
            if parsed.notna().sum() >= threshold:
                return True
        return False

    @classmethod
    def _infer_from_values(cls, series: pd.Series) -> str:
        """Inferisce tipo da valori"""
        if series.dropna().empty:
            return 'str'

        sample = str(series.dropna().iloc[0])

        sample_upper = sample.strip().upper()
        if any(sample_upper.startswith(prefix) for prefix in cls.WKT_PREFIXES):
            return 'geometry'

        if len(sample) >= 8 and any(c in sample for c in ['-', '/']):
            if cls._looks_like_date_column(series):
                return 'date'

        return 'str'

    @classmethod
    def detect_geometry_type(cls, geom_types: List[str]) -> str:
        """Mappa tipo geometria"""
        if not geom_types:
            return 'geometry'

        geom_type = geom_types[0].lower()
        return cls.GEOMETRY_TYPE_MAP.get(geom_type, 'geometry')


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


class ColumnInfoBuilder:
    """Costruisce info colonne - separato per testabilita'"""

    @staticmethod
    def from_dataframe(
        df: pd.DataFrame,
        gdf: Any = None,
        user_srid: Optional[int] = None
    ) -> List[ColumnInfo]:
        """Estrae info colonne da DataFrame"""
        columns = []
        geom_columns = {}

        if gdf is not None:
            geom_columns = ColumnInfoBuilder._extract_geometry_columns(gdf, user_srid)

        for col in df.columns:
            if col in geom_columns:
                col_type = geom_columns[col]
            else:
                col_type = TypeInferrer.infer_column_type(df[col])
                if col_type == 'geometry' and user_srid:
                    col_type = f"geometry:{user_srid}"

            columns.append(ColumnInfo(name=col, type=col_type))

        return columns

    @staticmethod
    def _extract_geometry_columns(gdf: Any, user_srid: Optional[int]) -> Dict[str, str]:
        """Estrae colonne geometriche da GeoDataFrame"""
        geom_columns = {}

        for col in gdf.columns:
            try:
                col_dtype = str(gdf[col].dtype)

                if col_dtype == 'geometry' or hasattr(gdf[col], 'geom_type'):
                    geom_series = gdf[col]
                    if not geom_series.empty:
                        geom_types = geom_series.dropna().apply(
                            lambda g: g.geom_type if g else None
                        ).dropna().unique().tolist()
                        geom_type = TypeInferrer.detect_geometry_type(geom_types)
                    else:
                        geom_type = 'geometry'

                    if gdf.crs:
                        srid = gdf.crs.to_epsg() or user_srid or 4326
                        geom_columns[col] = f"{geom_type}:{srid}"
                    elif user_srid:
                        geom_columns[col] = f"{geom_type}:{user_srid}"
                    else:
                        geom_columns[col] = geom_type
            except Exception as _e:
                # Rilevamento geometria fallito (non critico): il fallback e'
                # TypeInferrer.infer_column_type sulla colonna a valle
                _logger.debug(f"geometry detect fallita per colonna '{col}': {_e}")

        return geom_columns
