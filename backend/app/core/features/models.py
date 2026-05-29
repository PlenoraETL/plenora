"""
Modelli Pydantic per il modulo Features (ArcGIS Feature Service).

Gestisce connessioni ad ArcGIS Server, ArcGIS Enterprise e ArcGIS Online
"""

from pydantic import BaseModel
from typing import Any, Dict, List, Literal, Optional

from app.core.connections.arcgis_models import (
    ArcGISConnection,
    ArcGISCreate,
    ArcGISList,
    ArcGISTestResult,
    ArcGISUpdate,
)

__all__ = [
    "ArcGISConnection",
    "ArcGISCreate",
    "ArcGISList",
    "ArcGISTestResult",
    "ArcGISUpdate",
    "BrowseLayersResponse",
    "BrowseServicesResponse",
    "CreateServiceResponse",
    "FeatureWriteRequest",
    "ListFoldersResponse",
    "WriteFeaturesResponse",
]


# MODELLI PER SCRITTURA

class FeatureWriteRequest(BaseModel):
    """Parametri pubblici per la scrittura su un Feature Service."""
    connection_id: str
    service_url: str
    layer_id: int = 0
    operation: Literal["add", "update", "delete"] = "add"
    input_path: str = ""  # Path del file GeoDataFrame sorgente
    object_ids: Optional[List[int]] = None  # Per delete


class WriteFeaturesResponse(BaseModel):
    """Response di POST /api/features/write — fotografia 1:1 di
    `features_service.write_features` (4 branch ritornano questa stessa shape:
    add/update/delete + features-vuote). `errors` resta opaco (item Esri).
    """
    success: bool
    message: str
    count: int
    errors: List[Dict[str, Any]] = []


class CreateServiceResponse(BaseModel):
    """Response di POST /api/features/create-service — fotografia 1:1 di
    `arcgis_client.create_service` (single return path post createService +
    addToDefinition + addFeatures). `errors` resta opaco (item Esri).
    """
    success: bool
    service_url: str
    layer_url: str
    item_id: str
    feature_count: int
    errors: List[Dict[str, Any]] = []


class BrowseServicesResponse(BaseModel):
    """Wrapper triviale di GET /api/features/connections/{id}/services.
    Item ArcGIS opaco per natura: shape interna documentata `{name, url, folder}`
    ma ArcGIS può aggiungere campi non garantiti — `Dict[str, Any]` preserva
    tolleranza piena.
    """
    services: List[Dict[str, Any]]


class ListFoldersResponse(BaseModel):
    """Wrapper triviale di GET /api/features/connections/{id}/folders.
    Item ArcGIS opaco per natura: shape interna documentata `{id, title}`.
    """
    folders: List[Dict[str, Any]]


class BrowseLayersResponse(BaseModel):
    """Wrapper triviale di GET /api/features/connections/{id}/layers.
    Item ArcGIS opaco per natura: shape interna documentata
    `{id, name, geometry_type, feature_count, url}`.
    """
    layers: List[Dict[str, Any]]
