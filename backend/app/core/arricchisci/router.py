"""
Router API per il modulo ARRICCHISCI.

Unisce tutti gli endpoint relativi all'arricchimento dati:
"""
from app.core.arricchisci.canvas_preflight import prepare_contract, estimate_canvas, preparation_fingerprint
from app.core.projects.canvas_models import CanvasState
from app.core.projects.router import PREPARATION_ERRORS, _prepare_canvas_for_owner, raise_preparation_failure
from app.shared.execution.node_fields import node_field

import codecs
import json
import re
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, Query
from app.core.auth.dependencies import (
    require_not_esecutore,
    require_admin,
    require_user_or_above,
)
from app.shared.contracts.auth import CurrentUser
from app.core.auth.ownership import (
    assert_group_access,
    assert_resource_visible,
    assert_can_modify,
    compute_can_edit,
)
from pydantic import BaseModel, Field
from typing import Annotated, Any, List, Literal, Mapping, Optional

from app.utils.logger import get_logger
from app.utils.router_utils import require_service
from app.shared import error_codes
from app.shared.error_envelope import raise_credential_unavailable, raise_typed_http
from app.observability.audit_action import audit_action
from app.shared.connection_tests.results import (
    attaches_connection_tests,
    records_connection_test,
    resets_connection_test,
)
from app.core.auth.resource_access import assert_file_source_access, assert_project_visible
from app.infrastructure.security.crypto import CredentialUnavailableError
from app.core.arricchisci.api_connector import api_connection_service
from app.core.arricchisci.enrichment_service import EnrichmentExecutor
from app.core.arricchisci.models import EnrichmentOptions
from app.core.arricchisci.types import StringMap
from app.core.projects.exceptions import CanvasCorruptedError
from app.core.orchestrator.execution_contract import PipelineExecutionContract
from app.core.orchestrator.runtime_inputs import (
    ProjectTestRunNotApplicable,
    apply_project_runtime_inputs,
    apply_test_run_inputs,
    register_test_run_owner,
)
from app.shared.execution.runtime_identity import (
    TEST_RUN_NO_RECALCULATE_MESSAGE,
    RuntimeIdentity,
    RuntimeIdentityError,
)
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


# Specifiche OpenAPI/Swagger in JSON o YAML.
_OPENAPI_ACCEPT = (
    "application/json, application/yaml, application/x-yaml, text/yaml, "
    "application/vnd.oai.openapi+json, application/vnd.oai.openapi, */*;q=0.5"
)

# Byte order mark -> codifica: un BOM decide sempre (vince sul charset).
_SPEC_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
_CHARSET_RE = re.compile(r"""charset\s*=\s*["']?([^"';\s]+)""", re.IGNORECASE)


def decode_spec_bytes(content: bytes, content_type: str | None) -> tuple[str, bool]:
    """Testo di una specifica scaricata, decodificato dai BYTE.

    Regola unica per ogni Content-Type (JSON, YAML, text/plain, octet-stream):
    BOM se presente, altrimenti il charset dichiarato, altrimenti UTF-8 (la
    codifica di JSON e YAML). Se la decodifica stretta fallisce si ripiega su
    UTF-8 con i caratteri non validi sostituiti (U+FFFD) e il secondo valore
    e' True: il frontend lo segnala come avviso. Mai ISO-8859-1 implicito
    (il default di ``requests`` per ``text/*`` senza charset).
    """
    data = bytes(content or b"")
    encoding = next((enc for bom, enc in _SPEC_BOMS if data.startswith(bom)), None)
    if encoding is None:
        match = _CHARSET_RE.search(content_type or "")
        encoding = match.group(1) if match else "utf-8"
        try:
            codecs.lookup(encoding)
        except LookupError:
            encoding = "utf-8"
    try:
        return data.decode(encoding), False
    except (UnicodeDecodeError, LookupError):
        return data.decode("utf-8", errors="replace"), True


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
    # Risposta: il documento JSON gia' letto, oppure il testo cosi' com'e'
    # (una specifica YAML). Il frontend lo normalizza con l'unico importatore
    # (frontend/src/utils/openapiImport.js, Fase 5 P10): il backend non
    # interpreta la specifica.
    try:
        # verify=True (default) — niente più MITM via certificati invalidi
        r = await run_in_threadpool(
            lambda: request_pinned(
                "GET", body.url,
                headers={"Accept": _OPENAPI_ACCEPT},
                timeout=15,
                verify=True,
                max_response_bytes=8 * 1024 * 1024,
            )
        )
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code, detail=f"HTTP {r.status_code}")
        if 300 <= r.status_code < 400:
            # request_pinned non segue i reindirizzamenti (anti-SSRF): un 3xx
            # non e' una specifica vuota, e' un errore che indica l'URL finale.
            location = r.headers.get("location") or "un altro indirizzo"
            raise HTTPException(
                status_code=502,
                detail={
                    "error": error_codes.EXTERNAL_SERVICE_UNREACHABLE,
                    "message": (
                        f"La specifica risponde con un reindirizzamento (HTTP {r.status_code}) "
                        f"a {location}: usa l'URL finale."
                    ),
                },
            )
        text, replaced = decode_spec_bytes(r.content, r.headers.get("content-type"))
        if replaced:
            logger.warning("[GESTITO] Specifica OpenAPI non decodificabile: caratteri sostituiti")
        try:
            return json.loads(text)
        except ValueError:
            return text
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
    inputs: JsonObject = Field(default_factory=dict)
    canvas: JsonObject | None = None
    node_id: str | None = None


class EstimateRequest(BaseModel):
    project_id: str
    api_id: str
    param_mapping: StringMap
    file_source_id: Optional[str] = None
    inputs: JsonObject = Field(default_factory=dict)
    canvas: JsonObject | None = None
    node_id: str | None = None


class ValidateResponse(BaseModel):
    valid: bool
    total_rows: int
    valid_rows: int
    invalid_rows: int
    missing_columns: List[str]
    null_param_rows_count: int
    warnings: List[str]
    # Solo nel percorso per nodo (node_id/canvas). Il percorso legacy risponde
    # come HEAD: il campo non viene emesso (response_model_exclude_unset).
    status: str | None = None


class EstimateResponse(BaseModel):
    total_rows: int
    unique_combinations: int | None
    estimated_api_calls: int | None
    estimated_time_seconds: float | None
    estimated_time_formatted: str
    deduplication_savings_percent: float
    # Solo nel percorso per nodo; nel legacy non emessi (exclude_unset).
    status: str | None = None
    reason: str | None = None


# ENDPOINT: ENRICHMENT PREVIEW/VALIDATE/ESTIMATE

@enrichment_router.post("/validate", response_model=ValidateResponse, response_model_exclude_unset=True)
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

        if _is_node_request(request):
            # Percorso per nodo: preflight sul nodo richiesto e i suoi antenati.
            estimate = _compatibility_node_preflight(request, current_user)
            if (api_conn.get("api_type") or "enricher") == "generator":
                return {**_enrichment_preflight.generator_validate_response(), "valid": estimate["valid"],
                        "status": "configuration_verified"}
            validation = estimate.get("validation") or {}
            rows = estimate.get("input_rows") or 0
            return {"valid": estimate["valid"], "total_rows": rows,
                    "valid_rows": validation.get("valid_rows", rows), "invalid_rows": validation.get("invalid_rows", 0),
                    "missing_columns": validation.get("missing_columns", []),
                    "null_param_rows_count": len(validation.get("null_param_rows", [])),
                    "warnings": validation.get("warnings", []) + ([estimate["reason"]] if estimate.get("reason") else []),
                    "status": "configuration_verified" if estimate["status"] == "unknown" else "verified_with_data"}

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


@enrichment_router.post("/estimate", response_model=EstimateResponse, response_model_exclude_unset=True)
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

        if _is_node_request(request):
            estimate = _compatibility_node_preflight(request, current_user)
            count = estimate.get("api_calls")
            rate = (api_config.get("options") or {}).get("rate_limit") or 5
            return {"total_rows": estimate.get("input_rows") or 0, "unique_combinations": count,
                    "estimated_api_calls": count, "estimated_time_seconds": count / rate if count is not None else None,
                    "estimated_time_formatted": _format_time(count / rate) if count is not None else "non conoscibile", "deduplication_savings_percent": 0,
                    "status": estimate["status"], "reason": estimate.get("reason")}

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
@attaches_connection_tests("api_connection", items_field="api_connections")
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
@resets_connection_test("api_connection", id_param="api_id")
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
@resets_connection_test("api_connection", id_param="api_id")
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


def _tests_saved_api_configuration(kwargs: Mapping[str, Any]) -> bool:
    """Il test descrive la connessione SALVATA solo senza `config_overrides`
    (il wizard di modifica testa le modifiche non ancora salvate)."""
    body = kwargs.get("body")
    return not (isinstance(body, Mapping) and isinstance(body.get("config_overrides"), Mapping))


def _api_connection_secrets(api_id: str) -> Optional[JsonObject]:
    """Il connettore con l'auth in chiaro: i segreti da oscurare nell'esito
    salvato del test (connection_tests.results)."""
    try:
        return _api_connection_service.get_api_connection(api_id, include_token=True)
    except CredentialUnavailableError:
        return None


@api_connections_router.post("/api-connections/{api_id}/test")
@audit_action(action="api_connection.test", target_param="api_id")
@records_connection_test(
    "api_connection",
    id_param="api_id",
    secrets_of=_api_connection_secrets,
    when=_tests_saved_api_configuration,
)
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
    inputs: JsonObject = Field(default_factory=dict)
    format: str = "csv"
    force_recalculate: bool = False
    preparation_fingerprint: str | None = None


class EnrichPreflightRequest(BaseModel):
    inputs: JsonObject = Field(default_factory=dict)
    canvas: JsonObject | None = None
    force_recalculate: bool = False


def _is_node_request(request: ValidateRequest | EstimateRequest) -> bool:
    """Regola di compatibilità: senza ``node_id`` né ``canvas`` (il frontend
    attuale non li manda) validate/estimate rispondono esattamente come HEAD;
    con uno dei due passano dal preflight del nodo."""
    return request.node_id is not None or request.canvas is not None


def _ancestor_subcanvas(canvas: JsonObject, node_id: str) -> JsonObject:
    """Il nodo richiesto e i suoi antenati: gli errori di altri rami non contano."""
    edges = [edge for edge in canvas.get("edges", []) if isinstance(edge, dict)]
    keep, frontier = {node_id}, [node_id]
    while frontier:
        target = frontier.pop()
        for edge in edges:
            if edge.get("target") == target and edge.get("source") not in keep:
                keep.add(edge.get("source"))
                frontier.append(edge.get("source"))
    return {**canvas, "nodes": [node for node in canvas.get("nodes", []) if node.get("id") in keep],
            "edges": [edge for edge in edges if edge.get("source") in keep and edge.get("target") in keep]}


def _compatibility_node_preflight(request: ValidateRequest | EstimateRequest, current_user: CurrentUser) -> JsonObject:
    if not request.project_id:
        raise_typed_http(error_codes.VALIDATION_ERROR, status_code=422,
                         message="node_id/canvas richiedono project_id", details={"reason": "project_required"})
    canvas = request.canvas
    if canvas is None:
        saved = _project_service.get_project_canvas(request.project_id)
        if saved is None:
            raise HTTPException(404, "Canvas non trovato")
        canvas = saved.model_dump()
    canvas = json.loads(json.dumps(canvas, default=str))
    selected = [raw for raw in canvas.get("nodes", []) if isinstance(raw, dict) and raw.get("type") == "api_enrich"
                and (raw.get("id") == request.node_id if request.node_id else node_field(raw, "api_id") == request.api_id)]
    if len(selected) != 1:
        raise_typed_http(error_codes.VALIDATION_ERROR, status_code=422,
                         message="Indicare node_id: il nodo API da preparare non è univoco nel canvas",
                         details={"reason": "node_not_unique"})
    node_id = selected[0]["id"]
    selected[0]["data"] = {**(selected[0].get("data") or {}), "param_mapping": request.param_mapping}
    selected[0]["param_mapping"] = request.param_mapping
    subcanvas = _ancestor_subcanvas(canvas, node_id)
    # Stima del nodo nell'editor = anteprima: «Utente che esegue» vale lo
    # username di prova del nodo (runtime_identity).
    response = _preflight_enrichment(request.project_id,
                                     EnrichPreflightRequest(canvas=subcanvas, inputs=request.inputs),
                                     current_user, identity=RuntimeIdentity.preview_of(subcanvas))
    estimate = next((value for value in response["nodes"] if value["node_id"] == node_id), None)
    if estimate is None:
        raise_typed_http(error_codes.VALIDATION_ERROR, status_code=422, message="Nodo API non preparabile",
                         details={"reason": "node_not_prepared"})
    return estimate


def _preflight_connections(canvas: JsonObject, current_user: CurrentUser) -> JsonObject:
    connections = {}
    for node in canvas.get("nodes", []):
        if node.get("type") == "api_enrich":
            api_id = node_field(node, "api_id")
            if api_id not in connections:
                api = api_connection_service.get_api_connection(api_id, include_token=True)
                assert_resource_visible(api, "api_connections", current_user, f"API {api_id}")
                connections[api_id] = api
    return connections


@enrich_router.post("/{project_id}/preflight")
def preflight_enrichment(project_id: str, request: EnrichPreflightRequest,
                         current_user: CurrentUser = Depends(require_not_esecutore)) -> JsonObject:
    """Stima prima di «Esegui» del progetto: stessa identita' dell'avvio
    (``project_run``), un input «Utente che esegue» blocca gia' qui."""
    return _preflight_enrichment(project_id, request, current_user, identity=RuntimeIdentity.project_run())


def _refuse_test_run_recalculate(force_recalculate: bool) -> None:
    """La prova non ricalcola nulla a monte (runtime_identity, regola madre)."""
    if force_recalculate:
        raise_typed_http(error_codes.VALIDATION_ERROR, status_code=400, message=TEST_RUN_NO_RECALCULATE_MESSAGE)


class EnrichTestRunPreflightRequest(BaseModel):
    """Stima dell'«Esegui di prova»: nessun canvas (si usa quello SALVATO)."""

    model_config = {"extra": "forbid"}

    inputs: JsonObject = Field(default_factory=dict)
    force_recalculate: bool = False


@enrich_router.post("/{project_id}/test-run/preflight")
def preflight_enrichment_test_run(project_id: str, request: EnrichTestRunPreflightRequest,
                                  current_user: CurrentUser = Depends(require_user_or_above)) -> JsonObject:
    """Stima prima dell'«Esegui di prova» (runtime_identity, regola H): stessa
    preparazione dell'avvio di prova (identita' di prova del canvas salvato),
    stessa impronta. Solo chi puo' modificare il progetto."""
    _refuse_test_run_recalculate(request.force_recalculate)
    response = _preflight_enrichment(
        project_id,
        EnrichPreflightRequest(inputs=request.inputs, force_recalculate=request.force_recalculate),
        current_user,
        test_run=True,
    )
    return response


def _preflight_enrichment(project_id: str, request: EnrichPreflightRequest, current_user: CurrentUser, *,
                          identity: RuntimeIdentity | None = None, test_run: bool = False) -> JsonObject:
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")
    if metadata.project_type != "arricchisci":
        raise HTTPException(400, "Preflight disponibile per Arricchisci")
    if test_run and request.canvas is not None:
        # Lo username di prova viene SOLO dal canvas salvato (regola H).
        raise_typed_http(error_codes.VALIDATION_ERROR, status_code=400,
                         message="L'esecuzione di prova usa il canvas salvato: non si invia un canvas.")
    try:
        canvas = CanvasState(**request.canvas) if request.canvas is not None else _project_service.get_project_canvas(project_id)
        if canvas is None:
            raise HTTPException(404, "Canvas non trovato")
        canvas = _prepare_canvas_for_owner(project_id, canvas, metadata, current_user, for_save=False)
        contract, manifest = prepare_contract(PipelineExecutionContract(project_id=project_id,
            project_type=metadata.project_type, group_id=metadata.group_id, canvas=canvas.model_dump()), request.inputs,
            identity=identity, test_run=test_run)
        connections = _preflight_connections(contract.canvas, current_user)
        nodes = estimate_canvas(contract, connections)
    except RuntimeIdentityError as exc:
        raise_typed_http(exc.code, status_code=400, message=exc.message)
    except PREPARATION_ERRORS as exc:
        raise_preparation_failure(exc)
    response = {"valid": all(node["valid"] for node in nodes), "manifest": manifest.model_dump(), "nodes": nodes,
            "preparation_fingerprint": preparation_fingerprint(contract, connections, revision=_project_service.get_canvas_version(project_id), force_recalculate=request.force_recalculate),
            "checks": {"runtime_inputs": "verified", "external_dependencies": "not_guaranteed", "resources": "not_guaranteed"}}
    if contract.test_run_subject is not None:
        # La finestra «Prima di avviare» mostra l'utente che il SERVER usera'.
        response["test_run"] = {"username": contract.test_run_subject["username"]}
    return response


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
        inputs: valori runtime indicizzati per input_key
        format: formato output ('csv', 'xlsx', 'geojson', ...) — default 'csv'
        force_recalculate: ignora cache e ricalcola tutto — default False

    R-25 PR-B3: il param `intermediate_mode` e' opt-in superadmin per
    audit run-specific. Dual-auth: require_not_esecutore PER il base-flow
    + check superadmin SOLO se override richiesto.
    """
    return _launch_enrichment(project_id, request, intermediate_mode, current_user, test_run=False)


class EnrichTestRunRequest(BaseModel):
    """«Esegui di prova» di Arricchisci: canvas SALVATO, niente intermediate_mode."""

    model_config = {"extra": "forbid"}

    inputs: JsonObject = Field(default_factory=dict)
    format: str = "csv"
    force_recalculate: bool = False
    preparation_fingerprint: str | None = None


@enrich_router.post("/{project_id}/test-run/enrich")
@audit_action(action="enrich.test_run", target_param="project_id")
def launch_enrichment_test_run(
    project_id: str,
    request: EnrichTestRunRequest = None,
    current_user: CurrentUser = Depends(require_user_or_above),
) -> JsonObject:
    """«Esegui di prova» di Arricchisci (runtime_identity, regola H): solo chi
    puo' modificare il progetto, utente di prova del canvas salvato,
    risultato isolato (nessun output del progetto, nessun Carica)."""
    request = request or EnrichTestRunRequest()
    _refuse_test_run_recalculate(request.force_recalculate)
    return _launch_enrichment(
        project_id, EnrichAsyncRequest(**request.model_dump()), None, current_user, test_run=True,
    )


def _launch_enrichment(
    project_id: str,
    request: EnrichAsyncRequest | None,
    intermediate_mode: Optional[str],
    current_user: CurrentUser,
    *,
    test_run: bool,
) -> JsonObject:
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

        if not str(metadata.group_id or "").strip():
            raise HTTPException(
                status_code=400,
                detail="Il progetto non ha un gruppo proprietario: assegna un gruppo prima di eseguire.",
            )

        try:
            if request.preparation_fingerprint is not None:
                canvas = _prepare_canvas_for_owner(project_id, canvas, metadata, current_user, for_save=False)
            base_contract = PipelineExecutionContract(
                project_id=project_id,
                project_type=metadata.project_type,
                group_id=metadata.group_id,
                canvas=canvas.model_dump(),
            )
            if test_run:
                # «Esegui di prova»: utente di prova del canvas SALVATO.
                execution_contract = apply_test_run_inputs(base_contract, request.inputs)
            else:
                execution_contract = apply_project_runtime_inputs(
                    base_contract,
                    request.inputs,
                    identity=RuntimeIdentity.project_run(),
                )
        except RuntimeIdentityError as exc:
            raise_typed_http(exc.code, status_code=400, message=exc.message)
        except ProjectTestRunNotApplicable as exc:
            raise_typed_http(error_codes.VALIDATION_ERROR, status_code=400, message=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if request.preparation_fingerprint is not None:
            connections = _preflight_connections(execution_contract.canvas, current_user)
            fingerprint = preparation_fingerprint(execution_contract, connections, revision=_project_service.get_canvas_version(project_id),
                                                 force_recalculate=request.force_recalculate)
            if fingerprint != request.preparation_fingerprint:
                raise HTTPException(409, detail={"error": error_codes.VALIDATION_ERROR, "reason": "preparation_changed",
                    "message": "Preparazione cambiata: canvas, input, connessioni o opzioni diversi dal preflight"})

        reserved_task_id = None
        if test_run:
            # Il risultato isolato e' di chi ha lanciato la prova.
            reserved_task_id = task_service.reserve_task_id()
            register_test_run_owner(
                reserved_task_id,
                project_id=project_id,
                user_id=current_user.user_id,
                group_id=execution_contract.group_id,
            )
        task_info = task_service.create_enrichment_task(
            project_id=project_id,
            canvas=execution_contract.canvas,
            execution_contract=execution_contract.to_payload(),
            output_format=output_format,
            force_recalculate=request.force_recalculate,
            user_id=current_user.user_id,
            intermediate_mode_override=intermediate_mode,
            **({"task_id": reserved_task_id} if reserved_task_id else {}),
        )

        response = {
            "success": True, "async": True,
            "task_id": task_info.task_id,
            "status": task_info.status.value,
            "message": "Task avviato. Usa GET /api/tasks/{task_id} per monitorare il progress."
        }
        if test_run:
            response["test_run"] = {"username": execution_contract.test_run_subject["username"]}
        return response
        
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
