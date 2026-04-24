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
    group_id: str = "default"
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
    created_by: Optional[str] = None
    can_edit: Optional[bool] = None


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
