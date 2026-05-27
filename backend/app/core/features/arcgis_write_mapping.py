"""Compatibility re-export for the neutral ArcGIS write mapping boundary."""

from app.core.connections.arcgis_write_mapping import (
    ArcGISColumnMapping,
    ArcGISMappedFrame,
    ArcGISWriteMappingContract,
)

__all__ = [
    "ArcGISColumnMapping",
    "ArcGISMappedFrame",
    "ArcGISWriteMappingContract",
]
