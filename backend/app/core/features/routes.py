"""
Endpoint FastAPI per il modulo Features (ArcGIS Feature Service).

Architettura
============
API REST parallela alle route di connections/ per gestire connessioni ArcGIS
e operazioni su Feature Service.

Endpoint CRUD connessioni:
- POST   /api/features/connections              → crea connessione
- GET    /api/features/connections              → lista connessioni
- PUT    /api/features/connections/{id}         → aggiorna connessione
- DELETE /api/features/connections/{id}         → elimina connessione
- POST   /api/features/connections/{id}/test    → testa connessione

Endpoint browse:
- GET    /api/features/connections/{id}/services              → lista servizi
- GET    /api/features/connections/{id}/services/layers       → lista layer
- GET    /api/features/connections/{id}/services/layers/schema → schema campi

Endpoint dati:
- POST   /api/features/query/preview → preview features
- POST   /api/features/write         → scrivi su Feature Service

Sicurezza:
    Le password sono oscurate ('***') nelle risposte API.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List

from app.core.features.models import (
    ArcGISConnection,
    ArcGISCreate,
    ArcGISUpdate,
    ArcGISList,
    ArcGISTestResult,
    FeatureWriteRequest,
)
from app.core.features.features_service import FeaturesService

router = APIRouter(prefix="/features", tags=["features"])

# Istanza del servizio (singleton a livello di modulo)
_service: Optional[FeaturesService] = None


def get_service() -> FeaturesService:
    """Lazy init del FeaturesService."""
    global _service
    if _service is None:
        _service = FeaturesService()
    return _service


# =============================================================================
# HELPER
# =============================================================================

def _mask_password(conn: ArcGISConnection) -> dict:
    """Oscura la password nella risposta API."""
    data = conn.model_dump()
    if data.get("password"):
        data["password"] = "***"
    return data


# =============================================================================
# CRUD CONNESSIONI
# =============================================================================

@router.post("/connections", response_model=dict)
async def create_connection(data: ArcGISCreate):
    """Crea una nuova connessione ArcGIS."""
    try:
        connection = get_service().create_connection(data)
        return _mask_password(connection)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Errore interno del server")


@router.get("/connections", response_model=dict)
async def list_connections():
    """Lista tutte le connessioni ArcGIS."""
    try:
        result = get_service().list_connections()
        return {
            "connections": [_mask_password(c) for c in result.connections]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Errore interno del server")


@router.put("/connections/{connection_id}", response_model=dict)
async def update_connection(connection_id: str, data: ArcGISUpdate):
    """Aggiorna una connessione ArcGIS esistente."""
    connection = get_service().update_connection(connection_id, data)
    if not connection:
        raise HTTPException(status_code=404, detail="Connessione non trovata")
    return _mask_password(connection)


@router.delete("/connections/{connection_id}")
async def delete_connection(connection_id: str):
    """Elimina una connessione ArcGIS."""
    deleted = get_service().delete_connection(connection_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Connessione non trovata")
    return {"success": True, "message": "Connessione eliminata"}


@router.post("/connections/{connection_id}/test", response_model=dict)
async def test_connection(connection_id: str):
    """Testa una connessione ArcGIS."""
    try:
        result = get_service().test_connection(connection_id)
        return result.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Errore interno del server")


# =============================================================================
# BROWSE: SERVIZI E LAYER
# =============================================================================

@router.get("/connections/{connection_id}/services")
async def browse_services(connection_id: str):
    """
    Lista i Feature Service disponibili nella connessione.
    
    Equivalente a 'lista tabelle' per un database.
    Per ArcGIS Online: usa Content API.
    Per ArcGIS Server: naviga il catalogo REST.
    """
    try:
        services = get_service().browse_services(connection_id)
        return {"services": services}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Errore interno del server")


@router.get("/connections/{connection_id}/folders")
async def list_folders(connection_id: str):
    """Lista le cartelle dell'utente nella connessione ArcGIS."""
    try:
        folders = get_service().list_folders(connection_id)
        return {"folders": folders}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Errore interno del server")


@router.get("/connections/{connection_id}/layers")
async def browse_layers(
    connection_id: str,
    service_url: str = Query(..., description="URL del FeatureServer"),
):
    """
    Lista i layer di un Feature Service.
    
    Equivalente a 'lista colonne' per una tabella.
    """
    try:
        layers = get_service().browse_layers(connection_id, service_url)
        return {"layers": layers}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Errore interno del server")


@router.get("/connections/{connection_id}/schema")
async def get_layer_schema(
    connection_id: str,
    layer_url: str = Query(..., description="URL del layer (es. .../FeatureServer/0)"),
):
    """
    Recupera lo schema di un layer (campi + metadata).
    
    Equivalente a DatabaseInspector.get_tables_and_columns().
    """
    try:
        schema = get_service().get_layer_schema(connection_id, layer_url)
        return schema
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Errore interno del server")


# =============================================================================
# QUERY (lettura dati)
# =============================================================================

@router.post("/query/preview")
async def query_preview(
    connection_id: str = Query(...),
    layer_url: str = Query(...),
    where: str = Query("1=1"),
    out_fields: str = Query("*"),
    max_features: int = Query(50, description="Max features per preview"),
):
    """
    Preview dati da un layer (max N features).
    
    Restituisce righe e colonne per preview nella UI,
    equivalente a preview SQL in ESTRAI.
    """
    try:
        rows, columns = get_service().query_layer(
            connection_id, layer_url,
            where=where,
            out_fields=out_fields,
            return_geometry=True,
            max_features=max_features,
        )
        
        return {
            "success": True,
            "columns": columns,
            "rows": rows,
            "rows_count": len(rows),
        }
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Errore interno del server")


# =============================================================================
# SCRITTURA
# =============================================================================

class WriteRequest(BaseModel):
    """Request per scrittura su layer esistente."""
    connection_id: str
    layer_url: str  # URL completo del layer (es. .../FeatureServer/0)
    operation: str = "add"  # add, update, delete
    input_path: str = ""  # Path file sorgente (per add/update)
    project_id: str = ""  # Alternativa: cerca output in RESULTS_DIR/{project_id}
    geometry_column: str = "geometry"
    source_srid: Optional[int] = None  # SRID sorgente (dal nodo pipeline)
    object_ids: Optional[List[int]] = None  # Per delete
    where: Optional[str] = None  # Per delete


class CreateServiceRequest(BaseModel):
    """Request per creazione nuovo Feature Service."""
    connection_id: str
    service_name: str
    layer_name: str = "Layer 0"
    input_path: str = ""  # Path file sorgente
    project_id: str = ""  # Alternativa: cerca output in RESULTS_DIR/{project_id}
    geometry_column: str = "geometry"
    source_srid: Optional[int] = None  # SRID sorgente (dal nodo pipeline)


def _resolve_input_path(input_path: str, project_id: str) -> str:
    """
    Risolve il path del file input: usa input_path se fornito,
    altrimenti cerca il file output più recente nella directory risultati del progetto.
    Stesso pattern di carica_task._find_latest_output().
    """
    if input_path:
        return input_path
    
    if not project_id:
        raise ValueError("Specificare input_path o project_id")
    
    from app.utils.result_store import get_result_as_tempfile
    
    tmp = get_result_as_tempfile(project_id)
    if tmp:
        return str(tmp)
    
    raise FileNotFoundError(f"Nessun output trovato per progetto: {project_id}")


@router.post("/write")
async def write_features(request: WriteRequest):
    """
    Scrittura su Feature Service esistente (add/update/delete).
    
    Per add/update: legge il file da input_path (o da project_id), converte in Esri JSON,
    e invia al layer.
    Per delete: usa object_ids o where.
    """
    try:
        resolved_path = _resolve_input_path(request.input_path, request.project_id)
        
        result = get_service().write_features(
            connection_id=request.connection_id,
            layer_url=request.layer_url,
            operation=request.operation,
            input_path=resolved_path,
            geometry_column=request.geometry_column,
            object_ids=request.object_ids,
            where=request.where,
            source_srid=request.source_srid,
        )
        return result
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Errore interno del server")


@router.post("/create-service")
async def create_service(request: CreateServiceRequest):
    """
    Crea un nuovo Feature Service su ArcGIS Online.
    
    Flusso:
    1. Legge il file da input_path (o project_id)
    2. Crea il servizio con schema dal DataFrame
    3. Carica i dati nel layer
    
    Restituisce URL del nuovo servizio e conteggio features caricate.
    """
    try:
        resolved_path = _resolve_input_path(request.input_path, request.project_id)
        
        result = get_service().create_service(
            connection_id=request.connection_id,
            service_name=request.service_name,
            layer_name=request.layer_name,
            input_path=resolved_path,
            geometry_column=request.geometry_column,
            source_srid=request.source_srid,
        )
        return result
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Errore interno del server")