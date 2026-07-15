"""
Router API per il modulo ARRICCHISCI.

Unisce tutti gli endpoint relativi all'arricchimento dati:
"""

import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, Query
from app.core.auth.dependencies import (
    require_not_esecutore,
    require_admin,
)
from app.shared.contracts.auth import CurrentUser
from app.core.auth.ownership import (
    assert_group_access,
    assert_resource_visible,
    assert_can_modify,
    compute_can_edit,
)
from pydantic import BaseModel
from typing import Annotated, List, Literal, Optional

from app.utils.logger import get_logger
from app.utils.router_utils import require_service
from app.shared import error_codes
from app.shared.error_envelope import raise_credential_unavailable, raise_typed_http
from app.observability.audit_action import audit_action
from app.core.auth.resource_access import assert_file_source_access, assert_project_visible
from app.infrastructure.security.crypto import CredentialUnavailableError
from app.core.arricchisci.api_connector import api_connection_service
from app.core.arricchisci.enrichment_service import EnrichmentExecutor
from app.core.arricchisci.models import EnrichmentOptions
from app.core.arricchisci.types import StringMap
from app.core.projects.exceptions import CanvasCorruptedError
from app.core.artifacts import (
    LatestProjectArtifactRef,
    SourceLoader,
    SourceLoadingError,
    SourceRef,
)
from app.core.arricchisci import enrichment_preflight as _enrichment_preflight
from app.core.arricchisci.source_request_loader import load_request_data
from app.core.arricchisci.types import JsonObject
from app.core.auth.visibility import visible_resource_ids
from app.shared.security.outbound_url import validate_external_url
from app.infrastructure.http.session import request_pinned
from starlette.concurrency import run_in_threadpool


def _load_request_data(request, current_user) -> JsonObject:
    """Carica i dati sorgente per validate/estimate enrichment.

    Dispatcher 2-vie:
      - `file_source_id` valorizzato → carica quel file
      - altrimenti, `project_id` valorizzato → carica l'ultimo output del progetto
      - altrimenti → ritorna `None`

    Invariante "no fallback silenzioso": se entrambi sono valorizzati,
    `file_source_id` vince e `project_id` viene ignorato. Un caller che
    chiede un file_source specifico vuole quel file, non un mix con
    l'output del progetto. La regola e' enforced dal `if/elif/else`
    esplicito ed e' coperta dai contract test in
    `tests2/arricchisci/unit/test_arricchisci_router_security_contract.py`.

    `expected_group_id` propagato a `SourceLoader` come
    `current_user.group_id`, e `is_superadmin` come
    `current_user.role == "superadmin"`. Il loader fa il check di
    visibility (owned/shared/superadmin) — questo permette ai
    superadmin e ai progetti shared via `allowed_groups` di superare
    il check, allineando il behavior del loader a quello di
    `assert_project_visible` / `assert_file_source_access` gia' usati
    dai router come pre-check di accesso.

    Eccezioni `SourceLoadingError` propagate al caller (validate /
    estimate le traducono in HTTPException 400).
    """
    return load_request_data(
        request,
        current_user,
        source_loader_cls=SourceLoader,
        source_ref_cls=SourceRef,
        latest_project_artifact_ref_cls=LatestProjectArtifactRef,
    )

logger = get_logger(__name__)

# Sub-router separati per prefix diversi
enrichment_router = APIRouter(prefix="/enrichment", tags=["enrichment"])
api_connections_router = APIRouter(tags=["api-connections"])
templates_router = APIRouter(prefix="/templates", tags=["templates"])

# Servizi inizializzati da main.py
_project_service = None
_api_connection_service = None


def init_arricchisci(project_service, api_connection_service) -> None:
    """Inizializza il modulo. Chiamare da main.py dopo setup."""
    global _project_service, _api_connection_service
    _project_service = project_service
    _api_connection_service = api_connection_service


def _check_api_connection_ownership(api_id: str, current_user: CurrentUser) -> JsonObject:
    """Verifica modifiability sull'API connection (per operazioni di SCRITTURA).

    Usa hierarchical ownership (assert_can_modify): 404 se non esiste, 403 se
    appartiene a un altro gruppo oppure se è stata creata da un superadmin e
    l'utente corrente è solo admin di gruppo. I superadmin bypassano.

    Ritorna il config (mascherato) della connection.
    """
    connection = _api_connection_service.get_api_connection(api_id)
    assert_can_modify(connection, current_user, f"API connection '{api_id}'")
    return connection


def _check_api_connection_visible(api_id: str, current_user: CurrentUser) -> JsonObject:
    """Verifica visibilità sull'API connection (per operazioni di LETTURA: validate,
    estimate, preview, test, get).

    Considera owned + condivise via allowed_groups.
    Solleva 404 se non esiste, 403 se non visibile.
    """
    connection = _api_connection_service.get_api_connection(api_id)
    assert_resource_visible(connection, "api_connections", current_user, f"API connection '{api_id}'")
    return connection


def _check_project_ownership(project_id: str, current_user: CurrentUser) -> JsonObject:
    """Verifica che il progetto appartenga al gruppo dell'utente.

    Ritorna i metadata del progetto.
    """
    require_service(_project_service, "ProjectService")
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")
    return metadata


# PROXY — Fetch OpenAPI schema da URL esterni (evita blocco CORS nel browser

class FetchSchemaRequest(BaseModel):
    url: str

def _validate_external_url(url: str) -> None:
    """Anti-SSRF: wrapper che converte ValueError in HTTPException."""
    try:
        validate_external_url(url)
    except ValueError as e:
        msg = str(e)
        status = 403 if "IP privato" in msg or "bloccato" in msg else 400
        raise HTTPException(status, msg)


@api_connections_router.post("/api-connections/fetch-openapi-schema")
async def fetch_openapi_schema(body: FetchSchemaRequest, _user: CurrentUser = Depends(require_not_esecutore)):
    """Proxy per scaricare un OpenAPI schema da URL esterno, bypassando CORS.

    SECURITY:
      - Solo URL http/https (no file://, ftp://, ecc.)
      - Niente IP privati / loopback / link-local (anti-SSRF verso metadata
        cloud, servizi interni, Redis, Postgres, ecc.)
      - SSL verification ATTIVA (impedisce MITM su certificati invalidi)
    """
    try:
        # verify=True (default) — niente più MITM via certificati invalidi
        r = await run_in_threadpool(
            lambda: request_pinned(
                "GET", body.url,
                headers={"Accept": "application/json"},
                timeout=15,
                verify=True,
                max_response_bytes=8 * 1024 * 1024,
            )
        )
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code, detail=f"HTTP {r.status_code}")
        return r.json()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "[GESTITO] Errore fetch OpenAPI schema: %s",
            type(e).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error": error_codes.EXTERNAL_SERVICE_UNREACHABLE,
                "message": "Errore nel recupero dello schema OpenAPI",
            },
        )


# MODELLI REQUEST/RESPONSE — ENRICHMENT

class ValidateRequest(BaseModel):
    project_id: str
    api_id: str
    param_mapping: StringMap
    file_source_id: Optional[str] = None
    static_columns: Optional[List[str]] = None
    # static_parameters: dict {param_name: fixed_value}. Quando passato, le
    # sue chiavi sono trattate come 'colonne statiche' al pari di
    # static_columns. Evita di dover passare due liste in sync (lato FE) per
    # ogni nodo api_enrich.
    static_parameters: Optional[StringMap] = None


class EstimateRequest(BaseModel):
    project_id: str
    api_id: str
    param_mapping: StringMap
    file_source_id: Optional[str] = None


class ValidateResponse(BaseModel):
    valid: bool
    total_rows: int
    valid_rows: int
    invalid_rows: int
    missing_columns: List[str]
    null_param_rows_count: int
    warnings: List[str]


class EstimateResponse(BaseModel):
    total_rows: int
    unique_combinations: int
    estimated_api_calls: int
    estimated_time_seconds: float
    estimated_time_formatted: str
    deduplication_savings_percent: float


# ENDPOINT: ENRICHMENT PREVIEW/VALIDATE/ESTIMATE

@enrichment_router.post("/validate", response_model=ValidateResponse)
def validate_enrichment(
    request: ValidateRequest,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Valida input prima di eseguire l'enrichment."""
    try:
        api_conn = api_connection_service.get_api_connection(request.api_id)
        # Visibility check (read-only): owned + condivise via allowed_groups
        # Senza, un utente del tenant A potrebbe sondare api_id del tenant B
        assert_resource_visible(api_conn, "api_connections", current_user, f"API {request.api_id}")

        # Authz sulle risorse che verranno caricate da `_load_data`
        # senza questi check un utente con accesso a una API connection
        # possibilmente condivisa) potrebbe leakare canvas/file_source di
        # altri tenant passando project_id / file_source_id arbitrari
        if request.project_id:
            assert_project_visible(request.project_id, current_user)
        if request.file_source_id:
            assert_file_source_access(request.file_source_id, current_user)

        required_error = _enrichment_preflight.required_mapping_error(
            request,
            api_conn,
        )
        if required_error is not None:
            return required_error

        if (api_conn.get("api_type") or "enricher") == "generator":
            return _enrichment_preflight.generator_validate_response()

        try:
            df = _load_request_data(request, current_user)
        except SourceLoadingError as e:
            raise HTTPException(status_code=400, detail=str(e))

        return _enrichment_preflight.build_validate_response(request, df)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore validazione: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@enrichment_router.post("/estimate", response_model=EstimateResponse)
def estimate_enrichment(
    request: EstimateRequest,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Stima tempo di esecuzione senza chiamate API."""
    try:
        # include_token=True: codice backend interno, le credenziali non vanno mascherate
        # anche se estimate non chiama l'API, EnrichmentExecutor le legge nel costruttore
        api_config = api_connection_service.get_api_connection(request.api_id, include_token=True)
        # Visibility check (read-only stima): owned + condivise
        assert_resource_visible(api_config, "api_connections", current_user, f"API {request.api_id}")

        # Authz sulle risorse che verranno caricate da _load_request_data
        if request.project_id:
            assert_project_visible(request.project_id, current_user)
        if request.file_source_id:
            assert_file_source_access(request.file_source_id, current_user)

        try:
            df = _load_request_data(request, current_user)
        except SourceLoadingError as e:
            raise HTTPException(status_code=400, detail=str(e))

        return _enrichment_preflight.build_estimate_response(
            request,
            api_config,
            df,
            executor_cls=EnrichmentExecutor,
            options_cls=EnrichmentOptions,
            format_time=_format_time,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore stima: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


# ENDPOINT: API CONNECTIONS CRUD

@api_connections_router.get("/api-connections")
def list_api_connections(
    category: str = None,
    connection_type: str = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Lista API connections visibili: owned dal gruppo + condivise via allowed_groups.
    Ogni record include `can_edit` calcolato per l'utente corrente."""
    try:
        is_super = current_user.role == "superadmin"
        if is_super:
            all_conns = _api_connection_service.list_api_connections(
                category=category, connection_type=connection_type, group_id=None,
                limit=limit, offset=offset,
            )
        else:
            visible_ids = visible_resource_ids(
                'api_connections',
                current_user.group_id,
                is_superadmin=False,
            )
            all_conns = _api_connection_service.list_api_connections(
                category=category,
                connection_type=connection_type,
                group_id=None,
                resource_ids=visible_ids,
                limit=limit,
                offset=offset,
            )

        for c in all_conns:
            c["can_edit"] = compute_can_edit(c, current_user)

        return {"api_connections": all_conns}
    except Exception as e:
        logger.error(f"[CRASH] Errore lista API connections: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@api_connections_router.get("/api-connections/{api_id}")
def get_api_connection(api_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Ottieni una API connection specifica. Read-only su risorsa visibile (owned + shared)."""
    try:
        connection = _check_api_connection_visible(api_id, current_user)
        if connection is not None:
            connection["can_edit"] = compute_can_edit(connection, current_user)
        return connection
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore lettura API connection {api_id}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@api_connections_router.post("/api-connections", status_code=201)
@audit_action(action="api_connection.create")
def create_api_connection(data: JsonObject, current_user: CurrentUser = Depends(require_admin)):
    """Crea una nuova API connection. Solo admin/superadmin.

    `connection_type='executable'` non è più supportato (rimosso per
    ridurre la superficie RCE). Le richieste che lo specificano vengono
    rifiutate con 400.
    """
    if data.get("connection_type") == "executable":
        raise HTTPException(
            400,
            "connection_type 'executable' non è più supportato. "
            "Usa 'rest_file' per processori file→file esposti via HTTP."
        )
    try:
        data["group_id"] = current_user.group_id
        data["created_by"] = current_user.user_id
        connection = _api_connection_service.create_api_connection(data)
        if connection is not None:
            connection["can_edit"] = compute_can_edit(connection, current_user)
        return connection
    except Exception as e:
        logger.error(f"[CRASH] Errore creazione API connection: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@api_connections_router.put("/api-connections/{api_id}")
@audit_action(action="api_connection.update", target_param="api_id")
def update_api_connection(
    api_id: str,
    data: JsonObject,
    current_user: CurrentUser = Depends(require_admin),
):
    """Aggiorna una API connection esistente. Solo admin/superadmin.

    `connection_type='executable'` non è più supportato (rimosso per
    ridurre la superficie RCE). Le richieste che tentano di
    impostarlo vengono rifiutate con 400.
    """
    if data.get("connection_type") == "executable":
        raise HTTPException(
            400,
            "connection_type 'executable' non è più supportato. "
            "Usa 'rest_file' per processori file→file esposti via HTTP."
        )
    try:
        # Ownership check: 404 se non esiste, 403 se appartiene ad altro gruppo
        # Senza questo check, qualunque utente autenticato potrebbe modificare
        # le API connections di altri tenant indovinando l'api_id
        _check_api_connection_ownership(api_id, current_user)
        # Difesa in profondità: impedisci al client di trasferire la connection
        # a un altro gruppo/owner via payload (escalation cross-tenant
        data.pop("group_id", None)
        data.pop("created_by", None)
        result = _api_connection_service.update_api_connection(api_id, data)
        if not result:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.API_CONNECTION_NOT_FOUND,
                    "message": f"API connection '{api_id}' non trovata",
                },
            )
        if result is not None:
            result["can_edit"] = compute_can_edit(result, current_user)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore aggiornamento API connection {api_id}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@api_connections_router.delete("/api-connections/{api_id}", status_code=204)
@audit_action(action="api_connection.delete", target_param="api_id")
def delete_api_connection(api_id: str, current_user: CurrentUser = Depends(require_admin)):
    """Elimina una API connection, con check se è in uso in qualche canvas. Solo admin/superadmin."""
    # Ownership check PRIMA di qualunque altra cosa: 404/403 se non sei
    # autorizzato. Senza questo, l'attacker potrebbe almeno sondare quali
    # api_id esistono via il messaggio "in_use_by" o "non trovata"
    _check_api_connection_ownership(api_id, current_user)

    # Controlla se l'api_id è referenziata in canvas di progetti arricchisci
    # 6.3: il try *interno* per-canvas resta fail-open legittimo
    # un canvas corrotto di UN altro progetto non deve bloccare la delete
    # Il try *esterno* invece promuove a fail-closed: se list_projects o
    # il loop stesso falliscono globalmente (DB down, pool exhausted), non
    # possiamo escludere dipendenze -> 500, non silent-proceed
    in_use_by = []
    check_failed = False
    try:
        if _project_service:
            all_projects = _project_service.list_projects()
            proj_list = all_projects.projects if hasattr(all_projects, 'projects') else all_projects
            for proj in proj_list:
                # Supporta sia oggetti Pydantic che dict
                proj_dict = proj.model_dump() if hasattr(proj, 'model_dump') else (proj if isinstance(proj, dict) else vars(proj))
                proj_id   = proj_dict.get('project_id') or proj_dict.get('id')
                proj_type = proj_dict.get('project_type')
                if proj_type != 'arricchisci':
                    continue
                try:
                    canvas = _project_service.get_project_canvas(proj_id)
                except CanvasCorruptedError as _canvas_err:
                    # 6.4: narrow catch tipizzato (era isinstance
                    # inside broad). Canvas corrotto di un progetto != della
                    # api connection che stiamo cancellando → non blocchiamo
                    # la delete, ma logghiamo per triage
                    logger.warning(
                        f"delete_api_connection: canvas corrotto "
                        f"per progetto {proj_id} skippato "
                        f"({_canvas_err.reason})"
                    )
                    continue
                except Exception as _canvas_err:
                    # Altre eccezioni di lettura canvas: comportamento
                    # invariato (per-consumer fail-open), messaggio log
                    # generico per triage
                    logger.warning(
                        f"delete_api_connection: errore lettura canvas "
                        f"{proj_id}: {_canvas_err}"
                    )
                    continue
                if not canvas:
                    continue
                nodes_raw = canvas.nodes if hasattr(canvas, 'nodes') else canvas.get('nodes', [])
                for node in nodes_raw:
                    # I nodi possono essere oggetti Pydantic o dict
                    node_dict = node.model_dump() if hasattr(node, 'model_dump') else (node if isinstance(node, dict) else {})
                    node_api_id = (
                        node_dict.get('api_id') or
                        (node_dict.get('data') or {}).get('api_id')
                    )
                    if node_api_id == api_id:
                        proj_name = proj_dict.get('name', proj_id)
                        # project_type uniforma la shape 409 con
                        # connection/datasource; group_id abilita filtro
                        # cross-tenant nel detail.
                        in_use_by.append({
                            'project_id': proj_id,
                            'project_name': proj_name,
                            'project_type': proj_type,
                            'group_id': proj_dict.get('group_id', ''),
                        })
                        break
    except Exception as e:
        check_failed = True
        logger.error(
            f"[CRASH] delete_api_connection: impossibile verificare "
            f"dipendenze per {api_id}: {e}",
            exc_info=True,
        )

    if check_failed:
        raise HTTPException(
            status_code=500,
            detail="Impossibile verificare dipendenze: riprova piu' tardi",
        )

    if in_use_by:
        # Filtra dependencies per visibility lato chiamante: una API connection
        # puo' essere condivisa con altri gruppi via allowed_groups.
        is_super = current_user.role == "superadmin"
        if is_super:
            visible = list(in_use_by)
            hidden_count = 0
        else:
            visible = [p for p in in_use_by if p.get("group_id") == current_user.group_id]
            hidden_count = len(in_use_by) - len(visible)

        # Shape 409 cascade canonica: error + dependencies con kind,
        # allineata a projects/connections/datasources. `hidden_count`
        # resta opzionale e backward-compatible.
        raise HTTPException(
            status_code=409,
            detail={
                "error": error_codes.API_CONNECTION_IN_USE,
                "message": (
                    f"La connessione è usata in {len(in_use_by)} progetto/i. "
                    f"Rimuoverla dal canvas prima di eliminarla."
                ),
                "dependencies": [
                    {
                        "kind": "project",
                        "id": p["project_id"],
                        "name": p["project_name"],
                        # project_type uniforma la shape 409 cascade.
                        "project_type": p.get("project_type", ""),
                    }
                    for p in visible
                ],
                "hidden_count": hidden_count,
            },
        )

    if not _api_connection_service.delete_api_connection(api_id):
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.API_CONNECTION_NOT_FOUND,
                "message": f"API connection '{api_id}' non trovata",
            },
        )
    # DELETE risponde 204 No Content, body vuoto.


@api_connections_router.post("/api-connections/test")
def test_api_config(
    data: JsonObject,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Testa una configurazione API senza salvarla.

    SECURITY:
      - Richiede autenticazione (require_not_esecutore)
      - Anti-SSRF: l'URL fornito viene validato contro IP privati/interni
        (vedi _validate_external_url). Senza questo check, un utente loggato
        potrebbe far chiamare al server URL come http://169.254.169.254/
        (EC2 metadata), http://localhost:6379/ (Redis), ecc.
    """
    url = data.get("url")
    _validate_external_url(url)
    try:
        config = {
            "endpoint": {"url": url, "method": data.get("method", "GET")},
            "auth": data.get("auth", {"type": "none"}),
            "custom_headers": data.get("custom_headers", {}),
            "static_parameters": data.get("static_parameters", {}),
            "options": data.get("options", {}),
            "request": data.get("request", {}),
        }
        if data.get("async") is not None:
            config["async"] = data["async"]
        if data.get("success_when") is not None:
            config["success_when"] = data["success_when"]
        if data.get("error_path") is not None:
            config["error_path"] = data["error_path"]
        test_params = data.get("test_params", {})
        return _api_connection_service.test_api_connection(config=config, test_params=test_params)
    except HTTPException:
        raise
    except CredentialUnavailableError as e:
        raise_credential_unavailable(e)
    except Exception as e:
        logger.error(f"[CRASH] Errore test API config: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@api_connections_router.post("/api-connections/{api_id}/test")
@audit_action(action="api_connection.test", target_param="api_id")
def test_api_connection(
    api_id: str,
    body: Optional[JsonObject] = None,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Testa una API connection salvata per ID.

    Endpoint sensibile: triggera una chiamata HTTP REALE verso l'API
    esterna usando le credenziali decifrate del tenant. Solo chi vede
    la risorsa (owned o condivisa via allowed_groups) può testarla.
    """
    try:
        _check_api_connection_visible(api_id, current_user)
        payload = body or {}
        test_params = payload.get("test_params", {})
        config_overrides = payload.get("config_overrides")
        if isinstance(config_overrides, dict):
            override_url = config_overrides.get("url")
            endpoint = config_overrides.get("endpoint")
            if not override_url and isinstance(endpoint, dict):
                override_url = endpoint.get("url")
            if override_url:
                _validate_external_url(override_url)
            return _api_connection_service.test_api_connection_with_overrides(
                api_id=api_id,
                config_overrides=config_overrides,
                test_params=test_params,
            )
        return _api_connection_service.test_api_connection(api_id=api_id, test_params=test_params)
    except HTTPException:
        raise
    except CredentialUnavailableError as e:
        raise_credential_unavailable(e)
    except Exception as e:
        logger.error(f"[CRASH] Errore test API connection {api_id}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


# ENDPOINT: TEMPLATES API PRECONFIGURATI

TEMPLATES_FILE = Path(__file__).parent.parent.parent / "data" / "api_templates.json"


def _load_templates() -> list:
    """Carica i template dal file JSON."""
    try:
        if not TEMPLATES_FILE.exists():
            logger.warning(f"File template non trovato: {TEMPLATES_FILE}")
            return []
        with open(TEMPLATES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('templates', [])
    except Exception as e:
        logger.error(f"[CRASH] Errore caricamento template: {e}", exc_info=True)
        return []


@templates_router.get("")
def get_templates(current_user: CurrentUser = Depends(require_not_esecutore)):
    """Lista tutti i template API disponibili.

    I template sono dati statici (non legati a tenant), ma richiediamo auth
    per coerenza col resto dell'app: tutto sotto /api richiede login.
    """
    try:
        templates = _load_templates()
        return {"success": True, "count": len(templates), "templates": templates}
    except Exception as e:
        logger.error(f"[CRASH] Errore lista template: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


# ENDPOINT: ESECUZIONE ARRICCHIMENTO (sincrono/asincrono

enrich_router = APIRouter(prefix="/projects", tags=["arricchisci"])


class EnrichAsyncRequest(BaseModel):
    format: str = "csv"
    force_recalculate: bool = False


@enrich_router.post("/{project_id}/enrich-async")
@audit_action(action="enrich.execute_async", target_param="project_id")
def execute_enrichment_async(
    project_id: str,
    request: EnrichAsyncRequest = None,
    intermediate_mode: Optional[
        Literal["metadata_only", "full_artifact"]
    ] = Query(
        None,
        description=(
            "R-25 PR-B3: override per-call della INTERMEDIATE_PERSISTENCE_MODE "
            "globale. Solo superadmin (403 altrimenti). Mode invalido = 422 "
            "(Pydantic Literal). None = comportamento compat."
        ),
    ),
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """
    Esegue l'arricchimento in modo ASINCRONO con Celery.

    Parametri body (opzionali):
        format: formato output ('csv', 'xlsx', 'geojson', ...) — default 'csv'
        force_recalculate: ignora cache e ricalcola tutto — default False

    R-25 PR-B3: il param `intermediate_mode` e' opt-in superadmin per
    audit run-specific. Dual-auth: require_not_esecutore PER il base-flow
    + check superadmin SOLO se override richiesto.
    """
    require_service(_project_service, "ProjectService")
    require_service(_api_connection_service, "ApiConnectionService")
    # Auth + ownership: stesso ragionamento di /enrich sincrono
    _check_project_ownership(project_id, current_user)

    # Auth gate addizionale solo se intermediate_mode e' valorizzato
    if intermediate_mode is not None and current_user.role != "superadmin":
        raise HTTPException(
            status_code=403,
            detail="intermediate_mode override richiede ruolo superadmin",
        )

    from app.core.orchestrator.task_service import task_service

    # Valori di default se body non fornito
    if request is None:
        request = EnrichAsyncRequest()

    try:
        metadata = _project_service.get_project_metadata(project_id)
        if not metadata:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.PROJECT_NOT_FOUND,
                    "message": f"Progetto '{project_id}' non trovato",
                },
            )
        if metadata.project_type != "arricchisci":
            raise HTTPException(400, f"Progetto '{project_id}' non è di tipo 'arricchisci'")
        from app.core.orchestrator.service import canvas_validation_gate_blocks
        if canvas_validation_gate_blocks(getattr(metadata, "canvas_validated", False)):
            raise HTTPException(
                status_code=409,
                detail="Canvas non validato: apri il progetto e premi Valida prima di eseguire.",
            )

        canvas = _project_service.get_project_canvas(project_id)
        if not canvas:
            raise HTTPException(400, "Canvas non trovato")

        # Formato: usa il valore dal body se specificato
        # altrimenti tenta di leggerlo dal result_node del canvas
        output_format = request.format
        if not output_format or output_format == "csv":
            try:
                result_node = next(
                    (n for n in canvas.nodes if n.type == "result"),
                    None
                )
                if result_node:
                    canvas_format = (
                        getattr(result_node, "output_format", None)
                        or (result_node.data or {}).get("output_format")
                        or (result_node.data or {}).get("format")
                    )
                    if canvas_format:
                        output_format = canvas_format
            except Exception as _fmt_err:
                logger.debug(
                    f"Lettura output_format dal result_node fallita, "
                    f"fallback a '{output_format}': {_fmt_err}",
                    op="execute",
                )

        task_info = task_service.create_enrichment_task(
            project_id=project_id,
            canvas=canvas.model_dump(),
            output_format=output_format,
            force_recalculate=request.force_recalculate,
            user_id=current_user.user_id,
            intermediate_mode_override=intermediate_mode,
        )
        
        return {
            "success": True, "async": True,
            "task_id": task_info.task_id,
            "status": task_info.status.value,
            "message": "Task avviato. Usa GET /api/tasks/{task_id} per monitorare il progress."
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore execute_enrichment_async: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


# HELPER


def _format_time(seconds: float) -> str:
    """Formatta tempo in formato leggibile."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    else:
        return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"
