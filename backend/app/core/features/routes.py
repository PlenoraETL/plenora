"""
Endpoint FastAPI per il modulo Features (ArcGIS Feature Service).

Owns the HTTP boundary and delegates business logic to services or helpers.
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from typing import Annotated, Dict, Optional, List

from app.core.auth.dependencies import (
    require_not_esecutore,
    require_admin,
)
from app.shared.contracts.auth import CurrentUser
from app.core.auth.ownership import (
    assert_resource_visible,
    assert_can_modify,
    compute_can_edit,
)
from app.core.features.models import (
    WriteFeaturesResponse,
    CreateServiceResponse,
    BrowseServicesResponse,
    ListFoldersResponse,
    BrowseLayersResponse,
)
from app.core.connections.arcgis_models import (
    ArcGISConnection,
    ArcGISCreate,
    ArcGISList,
    ArcGISTestResult,
    ArcGISUpdate,
)
from app.core.features.features_service import FeaturesService
from app.core.features.arcgis_json_types import ArcGISJsonObject
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http
from app.observability.audit_action import audit_action
from app.shared.connection_tests.results import (
    attaches_connection_tests,
    records_connection_test,
    resets_connection_test,
)
from app.core.auth.resource_access import assert_project_visible
from app.shared.error_sanitizer import sanitize_http_detail
from app.core.artifacts import ArtifactResolver, LatestProjectArtifactRef
from app.core.auth.visibility import visible_resource_ids
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/features", tags=["features"])

# Istanza del servizio (singleton a livello di modulo
_service: Optional[FeaturesService] = None


def get_service() -> FeaturesService:
    """Lazy init del FeaturesService."""
    global _service
    if _service is None:
        _service = FeaturesService()
    return _service


# HELPER

def _mask_secrets(conn: ArcGISConnection) -> ArcGISJsonObject:
    """Oscura `password` e `client_secret` nelle response API delle
    connessioni ArcGIS: senza questa maschera un utente che legge la
    connection vedrebbe in chiaro il secret OAuth, anche se il DB e'
    cifrato at-rest.
    """
    data = conn.model_dump()
    if data.get("password"):
        data["password"] = "***"
    if data.get("client_secret"):
        data["client_secret"] = "***"
    return data


# CRUD CONNESSIONI

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


@router.post("/connections", response_model=ArcGISConnection, status_code=201)
@audit_action(action="arcgis.connection.create")
def create_connection(data: ArcGISCreate, current_user: CurrentUser = Depends(require_admin)):
    """Crea una nuova connessione ArcGIS. Solo admin/superadmin."""
    try:
        connection = get_service().create_connection(
            data,
            group_id=current_user.group_id,
            created_by=current_user.user_id,
        )
        if connection is None:
            raise HTTPException(status_code=500, detail="Errore creazione connessione")
        connection.can_edit = compute_can_edit(connection, current_user)
        return _mask_secrets(connection)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore creazione connessione ArcGIS: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.get("/connections", response_model=ArcGISList)
@attaches_connection_tests("arcgis_connection", items_field="connections")
def list_connections(
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Lista connessioni ArcGIS visibili: owned dal gruppo + condivise via allowed_groups.
    Ogni record include `can_edit` calcolato per l'utente corrente."""
    try:
        is_super = current_user.role == "superadmin"

        if is_super:
            result = get_service().list_connections(
                group_id=None, limit=limit, offset=offset,
            )
        else:
            visible_ids = visible_resource_ids(
                'arcgis_connections',
                current_user.group_id,
                is_superadmin=False,
            )
            result = get_service().list_connections(
                group_id=None,
                resource_ids=visible_ids,
                limit=limit,
                offset=offset,
            )

        for c in result.connections:
            c.can_edit = compute_can_edit(c, current_user)

        return {"connections": [_mask_secrets(c) for c in result.connections]}
    except Exception:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.put("/connections/{connection_id}", response_model=ArcGISConnection)
@audit_action(action="arcgis.connection.update", target_param="connection_id")
@resets_connection_test("arcgis_connection", id_param="connection_id")
def update_connection(connection_id: str, data: ArcGISUpdate, current_user: CurrentUser = Depends(require_admin)):
    """Aggiorna una connessione ArcGIS esistente. Solo admin/superadmin del gruppo,
    e solo se non creata da un superadmin (hierarchical ownership)."""
    _check_arcgis_owner(connection_id, current_user)
    connection = get_service().update_connection(connection_id, data)
    if not connection:
        raise HTTPException(status_code=404, detail="Connessione non trovata")
    connection.can_edit = compute_can_edit(connection, current_user)
    return _mask_secrets(connection)


@router.delete("/connections/{connection_id}", status_code=204)
@audit_action(action="arcgis.connection.delete", target_param="connection_id")
@resets_connection_test("arcgis_connection", id_param="connection_id")
def delete_connection(connection_id: str, current_user: CurrentUser = Depends(require_admin)):
    """Elimina una connessione ArcGIS. Solo admin/superadmin del gruppo,
    e solo se non creata da un superadmin (hierarchical ownership)."""
    _check_arcgis_owner(connection_id, current_user)
    deleted = get_service().delete_connection(connection_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Connessione non trovata")
    # DELETE risponde 204 No Content, body vuoto.


@router.post("/connections/{connection_id}/test", response_model=ArcGISTestResult)
@audit_action(action="arcgis.connection.test", target_param="connection_id")
@records_connection_test("arcgis_connection", id_param="connection_id")
def test_connection(connection_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Testa una connessione ArcGIS. Qualsiasi utente del gruppo."""
    _check_arcgis_access(connection_id, current_user)
    try:
        result = get_service().test_connection(connection_id)
        return result
    except Exception:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


# BROWSE: SERVIZI E LAYER

@router.get("/connections/{connection_id}/services", response_model=BrowseServicesResponse)
def browse_services(connection_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Lista i Feature Service disponibili nella connessione."""
    _check_arcgis_access(connection_id, current_user)
    try:
        services = get_service().browse_services(connection_id)
        return {"services": services}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=sanitize_http_detail(str(e)))
    except ConnectionError:
        raise HTTPException(
            status_code=502,
            detail={
                "error": error_codes.EXTERNAL_SERVICE_UNREACHABLE,
                "message": "Servizio esterno non raggiungibile",
            },
        )
    except Exception:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.get("/connections/{connection_id}/folders", response_model=ListFoldersResponse)
def list_folders(connection_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Lista le cartelle dell'utente nella connessione ArcGIS."""
    _check_arcgis_access(connection_id, current_user)
    try:
        folders = get_service().list_folders(connection_id)
        return {"folders": folders}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=sanitize_http_detail(str(e)))
    except ConnectionError:
        raise HTTPException(
            status_code=502,
            detail={
                "error": error_codes.EXTERNAL_SERVICE_UNREACHABLE,
                "message": "Servizio esterno non raggiungibile",
            },
        )
    except Exception:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.get("/connections/{connection_id}/layers", response_model=BrowseLayersResponse)
def browse_layers(
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
        raise HTTPException(status_code=404, detail=sanitize_http_detail(str(e)))
    except ConnectionError:
        raise HTTPException(
            status_code=502,
            detail={
                "error": error_codes.EXTERNAL_SERVICE_UNREACHABLE,
                "message": "Servizio esterno non raggiungibile",
            },
        )
    except Exception:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.get("/connections/{connection_id}/schema")
def get_layer_schema(
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
        raise HTTPException(status_code=404, detail=sanitize_http_detail(str(e)))
    except ConnectionError:
        raise HTTPException(
            status_code=502,
            detail={
                "error": error_codes.EXTERNAL_SERVICE_UNREACHABLE,
                "message": "Servizio esterno non raggiungibile",
            },
        )
    except Exception:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


# QUERY (lettura dati

@router.post("/query/preview")
def query_preview(
    connection_id: str = Query(...),
    layer_url: str = Query(...),
    where: str = Query("1=1"),
    out_fields: str = Query("*"),
    max_features: int = Query(50, ge=1, le=5000, description="Max features per preview"),
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
        raise HTTPException(status_code=404, detail=sanitize_http_detail(str(e)))
    except Exception:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


# SCRITTURA

class WriteRequest(BaseModel):
    """Request per scrittura su layer esistente."""
    connection_id: str
    layer_url: str  # URL completo del layer (es. .../FeatureServer/0
    operation: str = "add"  # add, update, upsert, delete
    upsert: bool = False  # Compat shorthand: true forces operation="upsert"
    input_path: str = ""  # Deprecated: local filesystem input is disabled
    project_id: str = ""  # Sorgente canonica: ultimo result blob del progetto
    geometry_column: str = "geometry"
    source_srid: Optional[int] = None  # SRID sorgente (dal nodo pipeline
    batch_size: Optional[int] = None
    match_column: str = ""
    column_mappings: List[Dict[str, object]] = Field(default_factory=list)
    object_ids: Optional[List[int]] = None  # Per delete
    where: Optional[str] = None  # Per delete


class CreateServiceRequest(BaseModel):
    """Request per creazione nuovo Feature Service."""
    connection_id: str
    service_name: str
    layer_name: str = "Layer 0"
    input_path: str = ""  # Deprecated: local filesystem input is disabled
    project_id: str = ""  # Sorgente canonica: ultimo result blob del progetto
    geometry_column: str = "geometry"
    source_srid: Optional[int] = None  # SRID sorgente (dal nodo pipeline
    column_mappings: List[Dict[str, object]] = Field(default_factory=list)


def _resolve_input_dataframe(
    input_path: str,
    project_id: str,
    *,
    expected_group_id: str,
):
    """Risolve l'input ArcGIS direttamente in DataFrame (bytes-first).

    Il flow bytes-first va da `materialize_latest(..., mode="bytes")` a
    `load_dataframe_from_artifact(
    data=..., format_hint=...)`. I 2 endpoint (`/write`, `/create-service`)
    usano poi `write_features_from_df` / `create_service_from_df`
    (entry-point dataframe-based gia' esistenti sul service).

    Args:
        input_path: rifiutato (filesystem locale non orizzontalizzabile).
        project_id: risolve l'ultimo output via resolver.
        expected_group_id: required, tenant binding fail-closed.

    Returns:
        `(df, format_hint)` tuple. `df` puo' essere DataFrame o GeoDataFrame
        a seconda del format dell'output upstream.

    Raises:
        ValueError: senza tenant binding, input_path locale, o nessun
            campo valorizzato.
        FileNotFoundError: project_id valido ma nessun output disponibile.
    """
    if not expected_group_id:
        raise ValueError(
            "_resolve_input_dataframe: expected_group_id obbligatorio "
            "(defense-in-depth)"
        )
    if project_id:
        materialized = ArtifactResolver().materialize_latest(
            LatestProjectArtifactRef(project_id=project_id),
            expected_group_id=expected_group_id,
            mode="bytes",
        )
        if materialized is None:
            raise FileNotFoundError(
                f"Nessun output trovato per progetto: {project_id}"
            )
        if materialized.df is not None:
            return materialized.df, materialized.format
        if materialized.data is not None:
            from app.core.dataframes.frame_io import (
                load_dataframe_from_artifact,
            )
            df = load_dataframe_from_artifact(
                data=materialized.data, format_hint=materialized.format,
            )
            return df, materialized.format
        raise FileNotFoundError(
            f"Nessun output trovato per progetto: {project_id}"
        )

    if input_path:
        raise ValueError(
            "input_path locale disabilitato: usare project_id/result blob "
            "come sorgente canonica"
        )

    raise ValueError("Specificare project_id o input_path")


@router.post("/write", response_model=WriteFeaturesResponse)
# target_param non disponibile: `connection_id` e' dentro al body Pydantic
# `request: WriteRequest`, l'extractor accetta solo str/int (vedi
# audit_action.py:190). Audit registra action+user+status, non il target
@audit_action(action="arcgis.feature.write")
def write_features(request: WriteRequest, current_user: CurrentUser = Depends(require_admin)):
    """Scrittura su Feature Service esistente (add/update/delete).

    SECURITY: require_admin (non require_not_esecutore). La scrittura su
    servizi esterni e' admin-only perche' puo' modificare dati condivisi via
    FeatureServer pubblico.
    """
    _check_arcgis_access(request.connection_id, current_user)
    # Se il caller risolve l'input via `project_id` invece che
    # `input_path`, verifichiamo l'accesso al progetto prima di
    # caricarne l'output: senza questo check, chi ha accesso alla
    # connessione ArcGIS potrebbe scrivere su un Feature Service
    # usando i dati di un progetto di un altro tenant conoscendone l'id
    if request.project_id:
        assert_project_visible(request.project_id, current_user)
    try:
        df, _fmt = _resolve_input_dataframe(
            request.input_path,
            request.project_id,
            expected_group_id=current_user.group_id,
        )
        operation = "upsert" if request.upsert else request.operation
        result = get_service().write_features_from_df(
            connection_id=request.connection_id,
            layer_url=request.layer_url,
            operation=operation,
            df=df,
            geometry_column=request.geometry_column,
            source_srid=request.source_srid,
            batch_size=request.batch_size,
            match_column=request.match_column,
            column_mappings=request.column_mappings,
        )
        return result

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=sanitize_http_detail(str(e)))
    except ConnectionError:
        raise HTTPException(
            status_code=502,
            detail={
                "error": error_codes.EXTERNAL_SERVICE_UNREACHABLE,
                "message": "Servizio esterno non raggiungibile",
            },
        )
    except Exception:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.post("/create-service", response_model=CreateServiceResponse)
@audit_action(action="arcgis.service.create")
def create_service(request: CreateServiceRequest, current_user: CurrentUser = Depends(require_admin)):
    """
    Crea un nuovo Feature Service su ArcGIS Online.

    SECURITY: require_admin (non require_not_esecutore). La creazione di
    un Feature Service su un account ArcGIS condiviso e' un'operazione
    cross-tenant (il nuovo servizio resta visibile a tutti gli utenti
    di quel portale). Allineato con `write_features` e con il pattern
    `create_connection`/`update_connection` del modulo.

    Flusso:
    1. Legge il file da input_path (o project_id)
    2. Crea il servizio con schema dal DataFrame
    3. Carica i dati nel layer

    Restituisce URL del nuovo servizio e conteggio features caricate.
    """
    _check_arcgis_access(request.connection_id, current_user)
    # Stesso check di write_features: se l'input arriva via project_id
    # validiamo l'accesso al progetto prima di leggerne l'output
    if request.project_id:
        assert_project_visible(request.project_id, current_user)
    try:
        df, _fmt = _resolve_input_dataframe(
            request.input_path,
            request.project_id,
            expected_group_id=current_user.group_id,
        )
        result = get_service().create_service_from_df(
            connection_id=request.connection_id,
            service_name=request.service_name,
            layer_name=request.layer_name,
            df=df,
            geometry_column=request.geometry_column,
            source_srid=request.source_srid,
            column_mappings=request.column_mappings,
        )
        return result

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=sanitize_http_detail(str(e)))
    except ConnectionError:
        raise HTTPException(
            status_code=502,
            detail={
                "error": error_codes.EXTERNAL_SERVICE_UNREACHABLE,
                "message": "Servizio esterno non raggiungibile",
            },
        )
    except Exception:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")
