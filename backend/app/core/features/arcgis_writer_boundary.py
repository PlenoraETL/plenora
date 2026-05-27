"""Compatibility re-export for the neutral ArcGIS feature writer boundary."""

from app.core.connections.arcgis_writer_boundary import (
    ArcGISColumnMappingsInput,
    ArcGISFeatureWritePublicResult,
    ArcGISFeatureWriter,
    ArcGISRawColumnMapping,
    normalize_arcgis_column_mappings,
)

__all__ = [
    "ArcGISColumnMappingsInput",
    "ArcGISFeatureWritePublicResult",
    "ArcGISFeatureWriter",
    "ArcGISRawColumnMapping",
    "normalize_arcgis_column_mappings",
]
