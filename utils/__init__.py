"""
Package utils - Utility centralizzate per DataFlow Manager.

Moduli:
- dataframe_utils: Operazioni su DataFrame (caricamento, salvataggio, filtri)
- geo_utils: Operazioni geospaziali (parsing, conversione GeoDataFrame)
- logger: Logging strutturato (già esistente)
"""

from utils.dataframe_utils import (
    load_dataframe_from_file,
    save_dataframe,
    filter_visible_columns,
    get_visible_column_names,
    sanitize_for_json,
    dataframe_to_json_records,
    infer_column_types,
    find_latest_output_file,
    get_column_from_node
)

from utils.geo_utils import (
    parse_geometry,
    to_geodataframe,
    detect_geometry_column,
    geometry_to_wkt,
    geometry_to_geojson,
    geodataframe_to_dataframe_wkt,
    ensure_same_crs,
    get_utm_crs_for_geometry,
    extract_srid_from_ewkb,
    extract_srid_from_column_type
)

__all__ = [
    # dataframe_utils
    'load_dataframe_from_file',
    'save_dataframe',
    'filter_visible_columns',
    'get_visible_column_names',
    'sanitize_for_json',
    'dataframe_to_json_records',
    'infer_column_types',
    'find_latest_output_file',
    'get_column_from_node',
    # geo_utils
    'parse_geometry',
    'to_geodataframe',
    'detect_geometry_column',
    'geometry_to_wkt',
    'geometry_to_geojson',
    'geodataframe_to_dataframe_wkt',
    'ensure_same_crs',
    'get_utm_crs_for_geometry',
    'extract_srid_from_ewkb',
    'extract_srid_from_column_type'
]