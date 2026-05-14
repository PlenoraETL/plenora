"""Compatibility facade for geospatial dataframe I/O helpers."""

from app.core.dataframes.geospatial_io import (
    _GEOMETRY_LIKE_COLUMN_NAMES,
    _compact_whole_number_float_properties,
    _converti_geometrie_secondarie_in_wkt,
    _dedup_columns_for_geo_save,
    _geo_helpers,
    _salva_formato_geo,
    _salva_geo_vuoto,
    _salva_shapefile_zippato,
    _sanitize_layer_name,
    _switch_colonna_geometria,
    geospatial_dataframe_to_bytes,
    save_geospatial_dataframe,
)

__all__ = [
    "_GEOMETRY_LIKE_COLUMN_NAMES",
    "_compact_whole_number_float_properties",
    "_converti_geometrie_secondarie_in_wkt",
    "_dedup_columns_for_geo_save",
    "_geo_helpers",
    "_salva_formato_geo",
    "_salva_geo_vuoto",
    "_salva_shapefile_zippato",
    "_sanitize_layer_name",
    "_switch_colonna_geometria",
    "geospatial_dataframe_to_bytes",
    "save_geospatial_dataframe",
]
