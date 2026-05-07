"""Pure helper classes per file source upload/inspection (STRUCT-001 Seam A).

Estratto verbatim da `app.services.file_service` senza alcun cambio
comportamentale. `file_service.py` re-esporta i 4 simboli per
backward-compat coi test e i moduli che li usano:

  - FormatDetector     mapping estensione -> format string ("csv", "gpkg", ...)
                        + classificazione geo/tabular.
  - TypeInferrer       pandas dtype -> type string ("int", "real", "bool",
                        "date", "geometry", "str") + WKT detection +
                        date detection conservativa con cascade di formati
                        espliciti (RB.#11) + classificazione geometry type.
  - FileIdGenerator    genera id opaco `file_<uuid_hex>` + sanitize_name
                        per uso in path (post N1: zero metadata leak).
  - ColumnInfoBuilder  DataFrame (+ GeoDataFrame opzionale + SRID utente)
                        -> List[ColumnInfo].

Leaf module: zero `app.services.*` imports. Cycle impossibile.

`ColumnInfo` viene importata da `app.models` quando disponibile, con
fallback a dataclass per esecuzione standalone (stesso pattern del
file_service originale).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd


# Logger stdlib: file_helpers e' un leaf module e non deve dipendere da
# `app.utils.logger`. I 4 helper sono pure functions; il logger e' usato
# solo per il debug-log non critico in `ColumnInfoBuilder._extract_geometry_columns`.
_logger = logging.getLogger(__name__)


# ColumnInfo: model di app.models (Pydantic in produzione) oppure dataclass
# fallback. Stesso pattern di file_service.py.
try:
    from app.models import ColumnInfo  # noqa: F401
    _USE_APP_MODELS = True
except ImportError:  # pragma: no cover
    _USE_APP_MODELS = False

if not _USE_APP_MODELS:
    @dataclass
    class ColumnInfo:  # type: ignore[no-redef]
        """Informazioni su una colonna (fallback dataclass)."""
        name: str
        type: str


class FormatDetector:
    """Rileva formato file - PURE FUNCTIONS"""

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
    """Inferisce tipi colonne - PURE FUNCTIONS"""

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

    # RB.#11 (2026-04-28): formati esplicitamente accettati per la
    # detection del tipo `date` durante upload. Cascata in ordine di
    # preferenza, ISO prima (universale, non-ambiguo) poi italiano.
    #
    # NON includiamo `%m/%d/%Y` (US): collide con `%d/%m/%Y` su date
    # come `"03/04/2025"` e su utenza italiana invertirebbe la scelta.
    # Se in futuro serve, va aggiunto un setting locale/date policy
    # esplicito, non un fallback ambiguo.
    #
    # Pre-fix: `pd.to_datetime(series.head(10))` senza format usava il
    # fallback dateutil interno di pandas 3.x, che:
    #   - emette `UserWarning: Could not infer format ...` ad ogni upload
    #   - sceglie month-first come default -> mm/dd US su utenza IT
    #   - parsa anche stringhe semi-numeriche tipo `"42-3-1"` -> falsi
    #     positivi tipo `date` su colonne che dovrebbero essere `str`.
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

        # Check WKT geometry
        sample_upper = sample.strip().upper()
        if any(sample_upper.startswith(prefix) for prefix in cls.WKT_PREFIXES):
            return 'geometry'

        # Check date - RB.#11: cascata di formati espliciti + soglia,
        # niente piu' fallback dateutil ambiguo / warning pandas.
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
    """Genera ID per file source - PURE (con time provider iniettato).

    N1 follow-up: format opaco `file_{uuid_hex}` - niente nome file
    ne' timestamp nell'ID.

    Pre-N1: `file_{safe_name}_{timestamp}_{uuid[:8]}`. Rivelava il
    nome file e l'orario di upload a chiunque vedesse l'ID in log o
    URL trafilati. Anche con fail-closed sul service, l'hygiene era
    scadente (metadata disclosure via ID).

    Post-N1-B: solo uuid hex. 2^128 entropia, zero info leak.
    NB: gli ID esistenti nel DB restano validi (lookup by string);
    solo i NUOVI file generati useranno il formato opaco.
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


# Analizza un DataFrame e costruisce la lista di ColumnInfo con tipo inferito.


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

        # Mappa colonne geometriche dal GeoDataFrame
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
                    # Rileva tipo geometria
                    geom_series = gdf[col]
                    if not geom_series.empty:
                        geom_types = geom_series.dropna().apply(
                            lambda g: g.geom_type if g else None
                        ).dropna().unique().tolist()
                        geom_type = TypeInferrer.detect_geometry_type(geom_types)
                    else:
                        geom_type = 'geometry'

                    # Determina SRID
                    if gdf.crs:
                        srid = gdf.crs.to_epsg() or user_srid or 4326
                        geom_columns[col] = f"{geom_type}:{srid}"
                    elif user_srid:
                        geom_columns[col] = f"{geom_type}:{user_srid}"
                    else:
                        geom_columns[col] = geom_type
            except Exception as _e:
                # Rilevamento geometria fallito (non critico): il fallback e'
                # TypeInferrer.infer_column_type() sulla colonna a valle.
                _logger.debug(f"geometry detect fallita per colonna '{col}': {_e}")

        return geom_columns
