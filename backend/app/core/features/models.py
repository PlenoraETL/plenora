"""
Modelli Pydantic per il modulo Features (ArcGIS Feature Service).

Gestisce connessioni ad ArcGIS Server, ArcGIS Enterprise e ArcGIS Online
per lettura/scrittura di Feature Service via REST API.

Architettura parallela a connections/models.py:
- ArcGISConnection    ↔ DatabaseConnection
- ArcGISCreate        ↔ ConnectionCreate
- ArcGISUpdate        ↔ ConnectionUpdate
- ArcGISList          ↔ ConnectionList
- ArcGISTestResult    ↔ ConnectionTestResult
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict, Any
from datetime import datetime


# =============================================================================
# MODELLI CONNESSIONE
# =============================================================================

class ArcGISConnection(BaseModel):
    """
    Configurazione completa di una connessione ArcGIS.
    
    Tipi di server:
    - arcgis_online: ArcGIS Online (https://www.arcgis.com)
    - arcgis_enterprise: ArcGIS Enterprise/Portal
    - arcgis_server: ArcGIS Server standalone (senza Portal)
    
    Tipi di autenticazione:
    - token: username/password → token REST
    - public: nessuna autenticazione (servizi pubblici)
    """
    id: str
    name: str
    description: str = ""
    server_url: str = Field(
        ..., 
        description="URL base del server (es. https://services.arcgis.com/xxx)"
    )
    server_type: Literal["arcgis_online", "arcgis_enterprise", "arcgis_server"] = "arcgis_server"
    auth_type: Literal["token", "oauth2", "public"] = "token"
    username: str = ""
    password: str = ""
    client_id: str = ""
    client_secret: str = ""
    token_url: Optional[str] = Field(
        None,
        description="URL custom per generazione token. Se None, viene auto-rilevato dal server_type"
    )
    created_at: datetime
    updated_at: datetime


class ArcGISCreate(BaseModel):
    """Dati richiesti per creare una nuova connessione ArcGIS."""
    name: str
    description: str = ""
    server_url: str
    server_type: Optional[Literal["arcgis_online", "arcgis_enterprise", "arcgis_server"]] = None
    auth_type: Literal["token", "oauth2", "public"] = "token"
    username: str = ""
    password: str = ""
    client_id: str = ""
    client_secret: str = ""
    token_url: Optional[str] = None


class ArcGISUpdate(BaseModel):
    """Dati parziali per aggiornare una connessione ArcGIS esistente."""
    name: Optional[str] = None
    description: Optional[str] = None
    server_url: Optional[str] = None
    server_type: Optional[Literal["arcgis_online", "arcgis_enterprise", "arcgis_server"]] = None
    auth_type: Optional[Literal["token", "oauth2", "public"]] = None
    username: Optional[str] = None
    password: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    token_url: Optional[str] = None


class ArcGISList(BaseModel):
    """Wrapper per la lista di connessioni ArcGIS."""
    connections: List[ArcGISConnection]


class ArcGISTestResult(BaseModel):
    """Risultato del test di connessione ArcGIS."""
    success: bool
    message: str
    details: Optional[dict] = None


# =============================================================================
# MODELLI PER BROWSING (lista servizi, layer, campi)
# =============================================================================

class ServiceInfo(BaseModel):
    """Informazioni su un Feature Service nel catalogo."""
    name: str
    url: str
    service_type: str = "FeatureServer"
    folder: str = ""


class LayerInfo(BaseModel):
    """Informazioni su un layer dentro un Feature Service."""
    id: int
    name: str
    geometry_type: str = ""  # esriGeometryPoint, esriGeometryPolygon, etc.
    feature_count: Optional[int] = None
    min_scale: float = 0
    max_scale: float = 0
    description: str = ""
    url: str = ""


class FieldInfo(BaseModel):
    """Informazioni su un campo di un layer."""
    name: str
    alias: str = ""
    field_type: str = ""     # esriFieldTypeString, esriFieldTypeInteger, etc.
    urby_type: str = ""      # Tipo interno URBY: str, int, real, geometry, etc.
    length: Optional[int] = None
    nullable: bool = True
    editable: bool = True


class LayerSchema(BaseModel):
    """Schema completo di un layer (campi + metadata)."""
    layer_id: int
    name: str
    geometry_type: str = ""
    spatial_reference: Optional[dict] = None
    fields: List[FieldInfo] = []
    feature_count: Optional[int] = None
    capabilities: str = ""  # "Query,Create,Update,Delete"


# =============================================================================
# MODELLI PER QUERY
# =============================================================================

class FeatureQueryRequest(BaseModel):
    """Parametri per una query su un layer."""
    connection_id: str
    service_url: str
    layer_id: int = 0
    where: str = "1=1"
    out_fields: str = "*"
    geometry_filter: Optional[dict] = None  # Bounding box o geometria filtro
    result_offset: int = 0
    result_record_count: Optional[int] = None
    order_by: Optional[str] = None
    return_geometry: bool = True
    out_sr: Optional[int] = None  # SRID di output
    project_id: Optional[str] = None  # ID progetto ESTRAI per salvare risultati (None = usa default)


class FeatureQueryResult(BaseModel):
    """Risultato di una query su un layer."""
    success: bool
    message: str
    rows_count: int = 0
    columns: List[str] = []
    output_path: str = ""
    timings: Dict[str, float] = {}


# =============================================================================
# MODELLI PER SCRITTURA
# =============================================================================

class FeatureWriteRequest(BaseModel):
    """Parametri per la scrittura su un Feature Service."""
    connection_id: str
    service_url: str
    layer_id: int = 0
    operation: Literal["add", "update", "delete"] = "add"
    input_path: str = ""  # Path del file GeoDataFrame sorgente
    object_ids: Optional[List[int]] = None  # Per delete


class FeatureWriteResult(BaseModel):
    """Risultato di un'operazione di scrittura."""
    success: bool
    message: str
    added_count: int = 0
    updated_count: int = 0
    deleted_count: int = 0
    errors: List[dict] = []