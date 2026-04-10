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

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import Optional, List

from app.core.auth.auth import (
    get_current_user,
    require_not_esecutore,
    require_admin,
    assert_group_access,
    assert_resource_visible,
    assert_can_modify,
    compute_can_edit,
    CurrentUser,
    group_filter,
)
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

def _check_arcgis_owner(connection_id: str, current_user: CurrentUser) -> ArcGISConnection:
    """
    Helper per operazioni di SCRITTURA: carica la connessione e verifica
    hierarchical ownership (assert_can_modify).
    Solleva 404 se non esiste, 403 se non del gruppo o creata da un superadmin
    quando l'utente corrente è solo admin di gruppo.
    """
    conn = get_service().get_connection(connection_id)
    assert_can_modify(conn, current_user, "Connessione ArcGIS")
    return conn


def _check_arcgis_access(connection_id: str, current_user: CurrentUser) -> ArcGISConnection:
    """
    Helper per operazioni di LETTURA (test, browse, schema, query): carica
    la connessione e verifica visibilità (owned + shared via allowed_groups).
    Solleva 404 se non esiste, 403 se non visibile.
    """
    conn = get_service().get_connection(connection_id)
    assert_resource_visible(conn, "arcgis_connections", current_user, "Connessione ArcGIS")
    return conn


@router.post("/connections", response_model=dict)
async def create_connection(data: ArcGISCreate, current_user: CurrentUser = Depends(require_admin)):
    """Crea una nuova connessione ArcGIS. Solo admin/superadmin."""
    try:
        connection = get_service().create_connection(
            data,
            group_id=current_user.group_id,
            created_by=current_user.user_id,
        )
        if connection is not None:
            connection.can_edit = compute_can_edit(connection, current_user)
        return _mask_password(connection)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Errore interno del server")


@router.get("/connections", response_model=dict)
async def list_connections(current_user: CurrentUser = Depends(require_not_esecutore)):
    """Lista connessioni ArcGIS visibili: owned dal gruppo + condivise via allowed_groups.
    Ogni record include `can_edit` calcolato per l'utente corrente."""
    from app.shared.sharing import visible_resource_ids

    try:
        is_super = current_user.role == "superadmin"

        result = get_service().list_connections(group_id=None)

        if not is_super:
            visible_ids = visible_resource_ids(
                'arcgis_connections',
                current_user.group_id,
                is_superadmin=False,
            )
            result.connections = [c for c in result.connections if c.id in visible_ids]

        for c in result.connections:
            c.can_edit = compute_can_edit(c, current_user)

        return {"connections": [_mask_password(c) for c in result.connections]}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Errore interno del server")


@router.put("/connections/{connection_id}", response_model=dict)
async def update_connection(connection_id: str, data: ArcGISUpdate, current_user: CurrentUser = Depends(require_admin)):
    """Aggiorna una connessione ArcGIS esistente. Solo admin/superadmin del gruppo,
    e solo se non creata da un superadmin (hierarchical ownership)."""
    _check_arcgis_owner(connection_id, current_user)
    connection = get_service().update_connection(connection_id, data)
    if not connection:
        raise HTTPException(status_code=404, detail="Connessione non trovata")
    connection.can_edit = compute_can_edit(connection, current_user)
    return _mask_password(connection)


@router.delete("/connections/{connection_id}")
async def delete_connection(connection_id: str, current_user: CurrentUser = Depends(require_admin)):
    """Elimina una connessione ArcGIS. Solo admin/superadmin del gruppo,
    e solo se non creata da un superadmin (hierarchical ownership)."""
    _check_arcgis_owner(connection_id, current_user)
    deleted = get_service().delete_connection(connection_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Connessione non trovata")
    return {"success": True, "message": "Connessione eliminata"}


@router.post("/connections/{connection_id}/test", response_model=dict)
async def test_connection(connection_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Testa una connessione ArcGIS. Qualsiasi utente del gruppo."""
    _check_arcgis_access(connection_id, current_user)
    try:
        result = get_service().test_connection(connection_id)
        return result.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Errore interno del server")


# =============================================================================
# BROWSE: SERVIZI E LAYER
# =============================================================================

@router.get("/connections/{connection_id}/services")
async def browse_services(connection_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Lista i Feature Service disponibili nella connessione."""
    _check_arcgis_access(connection_id, current_user)
    try:
        services = get_service().browse_services(connection_id)
        return {"services": services}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=502, detail="Servizio esterno non raggiungibile")
    except Exception as e:
        raise HTTPException(status_code=500, detail="Errore interno del server")


@router.get("/connections/{connection_id}/folders")
async def list_folders(connection_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Lista le cartelle dell'utente nella connessione ArcGIS."""
    _check_arcgis_access(connection_id, current_user)
    try:
        folders = get_service().list_folders(connection_id)
        return {"folders": folders}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=502, detail="Servizio esterno non raggiungibile")
    except Exception as e:
        raise HTTPException(status_code=500, detail="Errore interno del server")


@router.get("/connections/{connection_id}/layers")
async def browse_layers(
    connection_id: str,
    service_url: str = Query(..., description="URL del FeatureServer"),
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Lista i layer di un Feature Service."""
    _check_arcgis_access(connection_id, current_user)
    try:
        layers = get_service().browse_layers(connection_id, service_url)
        return {"layers": layers}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=502, detail="Servizio esterno non raggiungibile")
    except Exception as e:
        raise HTTPException(status_code=500, detail="Errore interno del server")


@router.get("/connections/{connection_id}/schema")
async def get_layer_schema(
    connection_id: str,
    layer_url: str = Query(..., description="URL del layer (es. .../FeatureServer/0)"),
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Recupera lo schema di un layer (campi + metadata)."""
    _check_arcgis_access(connection_id, current_user)
    try:
        schema = get_service().get_layer_schema(connection_id, layer_url)
        return schema
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=502, detail="Servizio esterno non raggiungibile")
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
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Preview dati da un layer (max N features)."""
    _check_arcgis_access(connection_id, current_user)
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
async def write_features(request: WriteRequest, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Scrittura su Feature Service esistente (add/update/delete)."""
    _check_arcgis_access(request.connection_id, current_user)
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
        raise HTTPException(status_code=502, detail="Servizio esterno non raggiungibile")
    except Exception as e:
        raise HTTPException(status_code=500, detail="Errore interno del server")


@router.post("/create-service")
async def create_service(request: CreateServiceRequest, current_user: CurrentUser = Depends(require_not_esecutore)):
    """
    Crea un nuovo Feature Service su ArcGIS Online.
    
    Flusso:
    1. Legge il file da input_path (o project_id)
    2. Crea il servizio con schema dal DataFrame
    3. Carica i dati nel layer

    Restituisce URL del nuovo servizio e conteggio features caricate.
    """
    _check_arcgis_access(request.connection_id, current_user)
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
        raise HTTPException(status_code=502, detail="Servizio esterno non raggiungibile")
    except Exception as e:
        raise HTTPException(status_code=500, detail="Errore interno del server")