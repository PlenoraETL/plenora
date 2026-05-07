"""
Endpoint FastAPI per il modulo Features (ArcGIS Feature Service).

Owns the HTTP boundary and delegates business logic to services or helpers.
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import Optional, List

from app.core.auth.dependencies import (
    get_current_user,
    require_not_esecutore,
    require_admin,
)
from app.shared.contracts.auth import CurrentUser, group_filter
from app.core.auth.ownership import (
    assert_group_access,
    assert_resource_visible,
    assert_can_modify,
    compute_can_edit,
)
from app.core.features.models import (
    ArcGISConnection,
    ArcGISCreate,
    ArcGISUpdate,
    ArcGISList,
    ArcGISTestResult,
    FeatureWriteRequest,
    WriteFeaturesResponse,
    CreateServiceResponse,
    BrowseServicesResponse,
    ListFoldersResponse,
    BrowseLayersResponse,
)
from app.core.features.features_service import FeaturesService
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http
from app.shared.audit_action import audit_action
from app.shared.resources.path_policy import assert_path_within
from app.shared.error_sanitizer import sanitize_http_detail
from app.core.artifacts import ArtifactResolver, LatestProjectArtifactRef
from app.core.artifacts.types import MaterializedArtifact
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

def _mask_secrets(conn: ArcGISConnection) -> dict:
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
async def create_connection(data: ArcGISCreate, current_user: CurrentUser = Depends(require_admin)):
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

        return {"connections": [_mask_secrets(c) for c in result.connections]}
    except Exception as e:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.put("/connections/{connection_id}", response_model=ArcGISConnection)
@audit_action(action="arcgis.connection.update", target_param="connection_id")
async def update_connection(connection_id: str, data: ArcGISUpdate, current_user: CurrentUser = Depends(require_admin)):
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
async def delete_connection(connection_id: str, current_user: CurrentUser = Depends(require_admin)):
    """Elimina una connessione ArcGIS. Solo admin/superadmin del gruppo,
    e solo se non creata da un superadmin (hierarchical ownership)."""
    _check_arcgis_owner(connection_id, current_user)
    deleted = get_service().delete_connection(connection_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Connessione non trovata")
    # F2-B2 (2026-04-24): DELETE risponde 204 No Content, body vuoto


@router.post("/connections/{connection_id}/test", response_model=ArcGISTestResult)
@audit_action(action="arcgis.connection.test", target_param="connection_id")
async def test_connection(connection_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Testa una connessione ArcGIS. Qualsiasi utente del gruppo."""
    _check_arcgis_access(connection_id, current_user)
    try:
        result = get_service().test_connection(connection_id)
        return result
    except Exception as e:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


# BROWSE: SERVIZI E LAYER

@router.get("/connections/{connection_id}/services", response_model=BrowseServicesResponse)
async def browse_services(connection_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Lista i Feature Service disponibili nella connessione."""
    _check_arcgis_access(connection_id, current_user)
    try:
        services = get_service().browse_services(connection_id)
        return {"services": services}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=sanitize_http_detail(str(e)))
    except ConnectionError as e:
        raise HTTPException(
            status_code=502,
            detail={
                "error": error_codes.EXTERNAL_SERVICE_UNREACHABLE,
                "message": "Servizio esterno non raggiungibile",
            },
        )
    except Exception as e:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.get("/connections/{connection_id}/folders", response_model=ListFoldersResponse)
async def list_folders(connection_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Lista le cartelle dell'utente nella connessione ArcGIS."""
    _check_arcgis_access(connection_id, current_user)
    try:
        folders = get_service().list_folders(connection_id)
        return {"folders": folders}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=sanitize_http_detail(str(e)))
    except ConnectionError as e:
        raise HTTPException(
            status_code=502,
            detail={
                "error": error_codes.EXTERNAL_SERVICE_UNREACHABLE,
                "message": "Servizio esterno non raggiungibile",
            },
        )
    except Exception as e:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.get("/connections/{connection_id}/layers", response_model=BrowseLayersResponse)
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
        raise HTTPException(status_code=404, detail=sanitize_http_detail(str(e)))
    except ConnectionError as e:
        raise HTTPException(
            status_code=502,
            detail={
                "error": error_codes.EXTERNAL_SERVICE_UNREACHABLE,
                "message": "Servizio esterno non raggiungibile",
            },
        )
    except Exception as e:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


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
        raise HTTPException(status_code=404, detail=sanitize_http_detail(str(e)))
    except ConnectionError as e:
        raise HTTPException(
            status_code=502,
            detail={
                "error": error_codes.EXTERNAL_SERVICE_UNREACHABLE,
                "message": "Servizio esterno non raggiungibile",
            },
        )
    except Exception as e:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


# QUERY (lettura dati

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
        raise HTTPException(status_code=404, detail=sanitize_http_detail(str(e)))
    except Exception as e:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


# SCRITTURA

class WriteRequest(BaseModel):
    """Request per scrittura su layer esistente."""
    connection_id: str
    layer_url: str  # URL completo del layer (es. .../FeatureServer/0
    operation: str = "add"  # add, update, delete
    input_path: str = ""  # Path file sorgente (per add/update
    project_id: str = ""  # Alternativa: cerca output in RESULTS_DIR/{project_id}
    geometry_column: str = "geometry"
    source_srid: Optional[int] = None  # SRID sorgente (dal nodo pipeline
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
    source_srid: Optional[int] = None  # SRID sorgente (dal nodo pipeline


def _resolve_input_path(
    input_path: str,
    project_id: str,
    *,
    expected_group_id: str,
) -> MaterializedArtifact:
    """Risolve il path del file di input per write/create-service ArcGIS.

    Ritorna un `MaterializedArtifact` **context-managed**: il caller
    DEVE usarlo in un blocco `with` per il cleanup del tempfile sul
    branch project_id (il resolver materializza il blob DB come
    `is_temporary=True`). Sul branch whitelist `is_temporary=False`
    e `__exit__` e' no-op.

    Dispatcher a 2 strategie:
      - `input_path` valorizzato → valida contro whitelist
        (RESULTS_DIR / TEMP_DIR / DATA_DIR / FILE_SOURCES_DIR) e
        ritorna un MaterializedArtifact non temporaneo
      - `project_id` valorizzato → risolve l'ultimo output via
        `ArtifactResolver.materialize_latest` con tenant binding
        fail-closed
      - entrambi vuoti → `ValueError("Specificare input_path o project_id")`

    `expected_group_id` keyword-only required: senza tenant binding
    il check cross-tenant del resolver sarebbe disabilitato (rifiuta
    None/blank).

    Security: `input_path` arbitrario dal client viene rifiutato fuori
    dalla whitelist, altrimenti un utente autenticato potrebbe leggere
    `/etc/passwd` o `/proc/self/environ` passando il path come parametro.

    Contratto eccezioni:
      - `input_path UNC non consentito` (UNC paths)
      - `input_path non consentito: ...` (fuori whitelist)
      - `input_path non valido: ...` (malformed)
      - `Specificare input_path o project_id` (entrambi vuoti)
      - `FileNotFoundError` (project_id valido, no output)
    """
    if not expected_group_id:
        raise ValueError(
            "_resolve_input_path: expected_group_id obbligatorio "
            "(defense-in-depth)"
        )
    from pathlib import Path as _Path
    from app.config import settings

    if input_path:
        # Whitelist di directory in cui e' lecito leggere file
        _allowed_roots = []
        for attr in ("RESULTS_DIR", "TEMP_DIR", "DATA_DIR", "FILE_SOURCES_DIR"):
            try:
                val = getattr(settings, attr, None)
                if val:
                    _allowed_roots.append(_Path(val).resolve())
            except Exception:
                pass

        # Validazione delegata a `assert_path_within`. Catturiamo
        # ValueError e re-raise con i messaggi specifici di questo
        # endpoint per preservare il contratto observable
        try:
            candidate = assert_path_within(input_path, _allowed_roots)
        except ValueError as e:
            msg = str(e)
            if "UNC" in msg:
                raise ValueError("input_path UNC non consentito") from e
            if "fuori dai trusted roots" in msg:
                raise ValueError(
                    "input_path non consentito: deve essere dentro una "
                    "directory di lavoro del backend (results/temp/data)"
                ) from e
            raise ValueError(f"input_path non valido: {e}") from e
        return MaterializedArtifact(path=candidate, is_temporary=False)

    if not project_id:
        raise ValueError("Specificare input_path o project_id")

    # Defense-in-depth: l'ACL a livello router (assert_project_visible
    # gia' garantisce la visibilita', ma passare expected_group_id al
    # resolver mantiene l'invariante cross-tenant uniforme anche se in
    # futuro questa funzione venisse richiamata da un path che salta
    # il check HTTP
    materialized = ArtifactResolver().materialize_latest(
        LatestProjectArtifactRef(project_id=project_id),
        expected_group_id=expected_group_id,
    )
    if materialized is not None and materialized.path is not None:
        return materialized

    raise FileNotFoundError(f"Nessun output trovato per progetto: {project_id}")


@router.post("/write", response_model=WriteFeaturesResponse)
# target_param non disponibile: `connection_id` e' dentro al body Pydantic
# `request: WriteRequest`, l'extractor accetta solo str/int (vedi
# audit_action.py:190). Audit registra action+user+status, non il target
@audit_action(action="arcgis.feature.write")
async def write_features(request: WriteRequest, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Scrittura su Feature Service esistente (add/update/delete)."""
    _check_arcgis_access(request.connection_id, current_user)
    # Se il caller risolve l'input via `project_id` invece che
    # `input_path`, verifichiamo l'accesso al progetto prima di
    # caricarne l'output: senza questo check, chi ha accesso alla
    # connessione ArcGIS potrebbe scrivere su un Feature Service
    # usando i dati di un progetto di un altro tenant conoscendone l'id
    if request.project_id:
        from app.shared.authz_helpers import assert_project_visible
        assert_project_visible(request.project_id, current_user)
    try:
        # `with` garantisce cleanup del tempfile quando il resolver
        # materializza l'ultimo output dal blob DB: senza questo
        # ogni chiamata con project_id lascerebbe un file orfano in
        # TEMP_DIR perche' `_resolve_input_path` perdeva il riferimento
        # al MaterializedArtifact
        with _resolve_input_path(
            request.input_path,
            request.project_id,
            expected_group_id=current_user.group_id,
        ) as materialized:
            result = get_service().write_features(
                connection_id=request.connection_id,
                layer_url=request.layer_url,
                operation=request.operation,
                input_path=str(materialized.path),
                geometry_column=request.geometry_column,
                object_ids=request.object_ids,
                where=request.where,
                source_srid=request.source_srid,
            )
        return result
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=sanitize_http_detail(str(e)))
    except ConnectionError as e:
        raise HTTPException(
            status_code=502,
            detail={
                "error": error_codes.EXTERNAL_SERVICE_UNREACHABLE,
                "message": "Servizio esterno non raggiungibile",
            },
        )
    except Exception as e:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.post("/create-service", response_model=CreateServiceResponse)
@audit_action(action="arcgis.service.create")
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
    # Stesso check di write_features: se l'input arriva via project_id
    # validiamo l'accesso al progetto prima di leggerne l'output
    if request.project_id:
        from app.shared.authz_helpers import assert_project_visible
        assert_project_visible(request.project_id, current_user)
    try:
        # Stesso pattern di write_features: `with` garantisce cleanup
        # del tempfile sul branch project_id (materialize_latest puo'
        # ritornare is_temporary=True
        with _resolve_input_path(
            request.input_path,
            request.project_id,
            expected_group_id=current_user.group_id,
        ) as materialized:
            result = get_service().create_service(
                connection_id=request.connection_id,
                service_name=request.service_name,
                layer_name=request.layer_name,
                input_path=str(materialized.path),
                geometry_column=request.geometry_column,
                source_srid=request.source_srid,
            )
        return result
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=sanitize_http_detail(str(e)))
    except ConnectionError as e:
        raise HTTPException(
            status_code=502,
            detail={
                "error": error_codes.EXTERNAL_SERVICE_UNREACHABLE,
                "message": "Servizio esterno non raggiungibile",
            },
        )
    except Exception as e:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")
