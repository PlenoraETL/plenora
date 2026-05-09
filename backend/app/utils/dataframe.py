"""
Compatibility facade for DataFrame helpers.

New code should prefer ``app.core.files.dataframe_io`` for pure tabular I/O and
``app.core.files.geospatial_io`` for geospatial export. This module keeps the
historic public API stable for existing workers, tests, and monkeypatch points.
"""

from pathlib import Path
from typing import Optional, Union

import pandas as pd

from app.core.files.dataframe_io import (
    _carica_da_zip,
    _carica_filegdb,
    _read_csv_safe,
    filter_visible_columns,
    find_latest_output_file,
    infer_column_types,
    load_dataframe_from_file,
    sanitize_for_json,
    save_tabular_dataframe,
    tabular_dataframe_to_bytes,
)
from app.core.files.geospatial_io import (
    _converti_geometrie_secondarie_in_wkt,
    _salva_formato_geo,
    _salva_geo_vuoto,
    _salva_shapefile_zippato,
    _sanitize_layer_name,
    _switch_colonna_geometria,
    geospatial_dataframe_to_bytes,
    save_geospatial_dataframe,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

_GEOSPATIAL_FORMATS = {"geojson", "gpkg", "shp"}


def save_dataframe(
    df: pd.DataFrame,
    output_path: Union[str, Path],
    format: str = "csv",
    geom_column: Optional[str] = None,
    crs: str = "EPSG:4326",
) -> Path:
    """Save a DataFrame while preserving the legacy facade contract."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    base_path = output_path.with_suffix("")
    formato = format.lower()

    try:
        if formato in _GEOSPATIAL_FORMATS:
            final_path = save_geospatial_dataframe(df, base_path, formato, geom_column, crs)
        else:
            final_path = save_tabular_dataframe(df, base_path, formato)

        logger.info(f"Salvato: {final_path} ({len(df)} righe)", op="save")
        return final_path

    except ValueError as e:
        logger.warning(f"Salvataggio non riuscito {output_path}: {e}")
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore salvataggio {output_path}: {e}", exc_info=True)
        raise


def save_dataframe_to_bytes(
    df: pd.DataFrame,
    format: str = "csv",
    geom_column: Optional[str] = None,
    crs: str = "EPSG:4326",
) -> bytes:
    """Serialize a DataFrame to bytes while preserving the legacy contract."""
    formato = format.lower()

    if formato in _GEOSPATIAL_FORMATS:
        return geospatial_dataframe_to_bytes(df, formato, geom_column, crs)

    return tabular_dataframe_to_bytes(df, formato)


__all__ = [
    "load_dataframe_from_file",
    "save_dataframe",
    "save_dataframe_to_bytes",
    "find_latest_output_file",
    "filter_visible_columns",
    "sanitize_for_json",
    "infer_column_types",
    "_read_csv_safe",
    "_carica_filegdb",
    "_carica_da_zip",
    "_sanitize_layer_name",
    "_salva_formato_geo",
    "_switch_colonna_geometria",
    "_converti_geometrie_secondarie_in_wkt",
    "_salva_geo_vuoto",
    "_salva_shapefile_zippato",
]
