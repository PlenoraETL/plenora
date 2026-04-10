"""
Router API per il modulo ARRICCHISCI.

Unisce tutti gli endpoint relativi all'arricchimento dati:
- Enrichment validate/estimate (da api/enrichment.py)
- API connections CRUD (da main.py)
- Template API preconfigurati (da api/templates.py)
- Esecuzione sincrona/asincrona arricchimento (da main.py)

Sub-router:
- /api/enrichment/*    → validazione e stima
- /api/api-connections → CRUD connessioni API
- /api/templates       → template preconfigurati (lista)
"""

import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends
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
from pydantic import BaseModel
from typing import Dict, Any, List, Optional

from app.config import settings
from app.utils.logger import get_logger, open_run, close_run, set_run_context
from app.utils.router_utils import require_service

logger = get_logger(__name__)

# Sub-router separati per prefix diversi
enrichment_router = APIRouter(prefix="/enrichment", tags=["enrichment"])
api_connections_router = APIRouter(tags=["api-connections"])
executables_router = APIRouter(prefix="/executables", tags=["executables"])
templates_router = APIRouter(prefix="/templates", tags=["templates"])

# Servizi inizializzati da main.py
_project_service = None
_api_connection_service = None


def init_arricchisci(project_service, api_connection_service):
    """Inizializza il modulo. Chiamare da main.py dopo setup."""
    global _project_service, _api_connection_service
    _project_service = project_service
    _api_connection_service = api_connection_service


def _check_api_connection_ownership(api_id: str, current_user: CurrentUser) -> dict:
    """Verifica modifiability sull'API connection (per operazioni di SCRITTURA).

    Usa hierarchical ownership (assert_can_modify): 404 se non esiste, 403 se
    appartiene a un altro gruppo oppure se è stata creata da un superadmin e
    l'utente corrente è solo admin di gruppo. I superadmin bypassano.

    Ritorna il config (mascherato) della connection.
    """
    connection = _api_connection_service.get_api_connection(api_id)
    assert_can_modify(connection, current_user, f"API connection '{api_id}'")
    return connection


def _check_api_connection_visible(api_id: str, current_user: CurrentUser) -> dict:
    """Verifica visibilità sull'API connection (per operazioni di LETTURA: validate,
    estimate, preview, test, get).

    Considera owned + condivise via allowed_groups.
    Solleva 404 se non esiste, 403 se non visibile.
    """
    connection = _api_connection_service.get_api_connection(api_id)
    assert_resource_visible(connection, "api_connections", current_user, f"API connection '{api_id}'")
    return connection


def _check_project_ownership(project_id: str, current_user: CurrentUser):
    """Verifica che il progetto appartenga al gruppo dell'utente.

    Ritorna i metadata del progetto.
    """
    require_service(_project_service, "ProjectService")
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")
    return metadata


# =============================================================================
# PROXY — Fetch OpenAPI schema da URL esterni (evita blocco CORS nel browser)
# =============================================================================

class FetchSchemaRequest(BaseModel):
    url: str

def _validate_external_url(url: str) -> None:
    """Anti-SSRF: valida che l'URL sia http(s) verso un host pubblico.

    Solleva HTTPException 400 se URL malformato, 403 se l'host risolve a un
    IP privato/loopback/link-local/riservato. Da usare prima di QUALSIASI
    fetch verso URL fornite dall'utente (proxy, test, webhook, ecc.).

    Bloccato:
      - schemi != http/https (file://, ftp://, gopher://, ...)
      - IP privati (10.x, 172.16-31.x, 192.168.x)
      - loopback (127.x)
      - link-local (169.254.x — incluso EC2/Azure metadata)
      - reserved/multicast
    """
    from urllib.parse import urlparse
    import ipaddress
    import socket

    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "URL non valido: solo schemi http/https sono permessi")
    host = parsed.hostname
    if not host:
        raise HTTPException(400, "URL non valido: hostname mancante")

    try:
        _, _, addr_list = socket.gethostbyname_ex(host)
    except socket.gaierror:
        raise HTTPException(400, f"Host non risolvibile: {host}")
    for addr in addr_list:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise HTTPException(
                403,
                f"URL bloccato: l'host risolve a un IP privato/interno ({addr})",
            )


@api_connections_router.post("/api-connections/fetch-openapi-schema")
async def fetch_openapi_schema(body: FetchSchemaRequest, user: CurrentUser = Depends(require_not_esecutore)):
    """Proxy per scaricare un OpenAPI schema da URL esterno, bypassando CORS.

    SECURITY:
      - Solo URL http/https (no file://, ftp://, ecc.)
      - Niente IP privati / loopback / link-local (anti-SSRF verso metadata
        cloud, servizi interni, Redis, Postgres, ecc.)
      - SSL verification ATTIVA (impedisce MITM su certificati invalidi)
    """
    import httpx

    _validate_external_url(body.url)

    try:
        # verify=True (default) — niente più MITM via certificati invalidi.
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            r = await client.get(body.url, headers={"Accept": "application/json"})
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"HTTP {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# =============================================================================
# MODELLI REQUEST/RESPONSE — ENRICHMENT
# =============================================================================

class ValidateRequest(BaseModel):
    project_id: str
    api_id: str
    param_mapping: Dict[str, str]
    file_source_id: Optional[str] = None
    static_columns: Optional[List[str]] = None


class EstimateRequest(BaseModel):
    project_id: str
    api_id: str
    param_mapping: Dict[str, str]
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


# =============================================================================
# ENDPOINT: ENRICHMENT PREVIEW/VALIDATE/ESTIMATE
# =============================================================================

@enrichment_router.post("/validate", response_model=ValidateResponse)
async def validate_enrichment(
    request: ValidateRequest,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Valida input prima di eseguire l'enrichment."""
    try:
        from app.core.arricchisci.api_connector import api_connection_service

        api_conn = api_connection_service.get_api_connection(request.api_id)
        # Visibility check (read-only): owned + condivise via allowed_groups.
        # Senza, un utente del tenant A potrebbe sondare api_id del tenant B.
        assert_resource_visible(api_conn, "api_connections", current_user, f"API {request.api_id}")

        # Verifica che tutti i parametri required dell'API abbiano un mapping non vuoto.
        # Quando l'utente lascia "-- colonna --" nel dropdown, il frontend omette
        # la chiave dal JSON (undefined → dropped by JSON.stringify), quindi il
        # param_mapping arriva incompleto senza che il backend se ne accorga.
        required_params = [
            p["name"] for p in (api_conn.get("parameters") or [])
            if p.get("required", False)
        ]
        static_cols_pre = set(request.static_columns or [])
        unmapped_required = [
            p for p in required_params
            if not request.param_mapping.get(p)          # mancante o stringa vuota
            and p not in static_cols_pre                  # non è un parametro statico
        ]
        if unmapped_required:
            return ValidateResponse(
                valid=False,
                total_rows=0, valid_rows=0, invalid_rows=0,
                missing_columns=unmapped_required,
                null_param_rows_count=0,
                warnings=[
                    f"Parametri obbligatori senza mapping: {', '.join(unmapped_required)}"
                ]
            )

        df = _load_data(request.project_id, request.file_source_id)
        
        if df is None or df.empty:
            static_cols = set(request.static_columns or [])
            all_static = all(col in static_cols for col in request.param_mapping.values())

            # B-BE-58: se df è vuoto ma esiste (0 righe), le colonne mappate potrebbero
            # comunque esistere nell'header — non segnalarle come mancanti.
            # Distingui df=None (sorgente non trovata) da df.empty (file con 0 righe).
            if df is not None and df.empty:
                # File caricato con 0 righe: verifica colonne dall'header
                missing = [col for col in request.param_mapping.values()
                           if col not in static_cols and col not in df.columns]
                return ValidateResponse(
                    valid=len(missing) == 0,
                    total_rows=0, valid_rows=0, invalid_rows=0,
                    missing_columns=missing,
                    null_param_rows_count=0,
                    warnings=["File sorgente vuoto (0 righe) — nessun dato da arricchire"]
                )

            # df is None: sorgente non trovata, validazione solo configurazione
            return ValidateResponse(
                valid=all_static or len(request.param_mapping) == 0,
                total_rows=0, valid_rows=0, invalid_rows=0,
                missing_columns=[] if all_static else list(request.param_mapping.values()),
                null_param_rows_count=0,
                warnings=["Nessun dato trovato nel progetto - validazione solo configurazione"] if not all_static else []
            )
        
        missing_columns = []
        static_cols = set(request.static_columns or [])
        
        for api_param, df_column in request.param_mapping.items():
            if df_column in static_cols:
                continue
            if df_column not in df.columns:
                missing_columns.append(df_column)
        
        # Se ci sono colonne mancanti, ritorna subito: il conteggio null
        # non è significativo e potrebbe confondere l'utente
        if missing_columns:
            return ValidateResponse(
                valid=False,
                total_rows=len(df),
                valid_rows=0,
                invalid_rows=len(df),
                missing_columns=missing_columns,
                null_param_rows_count=0,
                warnings=[f"Colonne mancanti nel dataset: {', '.join(missing_columns)}"]
            )
        
        null_count = 0
        for df_column in request.param_mapping.values():
            if df_column in df.columns:
                null_count += df[df_column].isna().sum()
        
        total_rows = len(df)
        null_rows = int(null_count)
        valid_rows = total_rows - null_rows if null_rows < total_rows else 0
        
        warnings = []
        if null_rows > 0:
            warnings.append(f"{null_rows} valori null nei parametri mappati — quelle righe verranno saltate")
        
        return ValidateResponse(
            valid=True,
            total_rows=total_rows,
            valid_rows=valid_rows,
            invalid_rows=null_rows,
            missing_columns=[],
            null_param_rows_count=null_rows,
            warnings=warnings
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore validazione: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@enrichment_router.post("/estimate", response_model=EstimateResponse)
async def estimate_enrichment(
    request: EstimateRequest,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Stima tempo di esecuzione senza chiamate API."""
    try:
        from app.core.arricchisci.api_connector import api_connection_service
        from app.core.arricchisci.enrichment_service import EnrichmentExecutor
        from app.core.arricchisci.models import EnrichmentOptions

        # include_token=True: codice backend interno, le credenziali non vanno mascherate
        # (anche se estimate non chiama l'API, EnrichmentExecutor le legge nel costruttore)
        api_config = api_connection_service.get_api_connection(request.api_id, include_token=True)
        # Visibility check (read-only stima): owned + condivise.
        assert_resource_visible(api_config, "api_connections", current_user, f"API {request.api_id}")

        df = _load_data(request.project_id, request.file_source_id)
        if df is None or df.empty:
            return EstimateResponse(
                total_rows=0, unique_combinations=0, estimated_api_calls=0,
                estimated_time_seconds=0, estimated_time_formatted="0s",
                deduplication_savings_percent=0
            )
        
        total_rows = len(df)
        executor = EnrichmentExecutor(
            api_config=api_config,
            param_mapping=request.param_mapping,
            options=EnrichmentOptions(enable_deduplication=True)
        )
        
        unique_combinations = executor._count_unique_combinations(df)
        rate_limit = api_config.get("options", {}).get("rate_limit", 5)
        estimated_time = unique_combinations / rate_limit if rate_limit > 0 else unique_combinations
        savings = ((total_rows - unique_combinations) / total_rows * 100) if total_rows > 0 else 0
        
        return EstimateResponse(
            total_rows=total_rows,
            unique_combinations=unique_combinations,
            estimated_api_calls=unique_combinations,
            estimated_time_seconds=estimated_time,
            estimated_time_formatted=_format_time(estimated_time),
            deduplication_savings_percent=round(savings, 1)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore stima: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# ENDPOINT: API CONNECTIONS CRUD
# =============================================================================

@api_connections_router.get("/api-connections")
def list_api_connections(category: str = None, connection_type: str = None, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Lista API connections visibili: owned dal gruppo + condivise via allowed_groups.
    Ogni record include `can_edit` calcolato per l'utente corrente."""
    try:
        from app.shared.sharing import visible_resource_ids

        is_super = current_user.role == "superadmin"
        all_conns = _api_connection_service.list_api_connections(
            category=category, connection_type=connection_type, group_id=None,
        )

        if not is_super:
            visible_ids = visible_resource_ids(
                'api_connections',
                current_user.group_id,
                is_superadmin=False,
            )
            all_conns = [c for c in all_conns if c.get("id") in visible_ids]

        for c in all_conns:
            c["can_edit"] = compute_can_edit(c, current_user)

        return {"api_connections": all_conns}
    except Exception as e:
        logger.error(f"[CRASH] Errore lista API connections: {e}", exc_info=True)
        raise HTTPException(500, str(e))


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
        raise HTTPException(500, str(e))


@api_connections_router.post("/api-connections")
def create_api_connection(data: dict, current_user: CurrentUser = Depends(require_admin)):
    """Crea una nuova API connection. Solo admin/superadmin."""
    try:
        data["group_id"] = current_user.group_id
        data["created_by"] = current_user.user_id
        connection = _api_connection_service.create_api_connection(data)
        if connection is not None:
            connection["can_edit"] = compute_can_edit(connection, current_user)
        return connection
    except Exception as e:
        logger.error(f"[CRASH] Errore creazione API connection: {e}", exc_info=True)
        raise HTTPException(500, str(e))


@api_connections_router.put("/api-connections/{api_id}")
def update_api_connection(api_id: str, data: dict, current_user: CurrentUser = Depends(require_admin)):
    """Aggiorna una API connection esistente. Solo admin/superadmin."""
    try:
        # Ownership check: 404 se non esiste, 403 se appartiene ad altro gruppo.
        # Senza questo check, qualunque utente autenticato potrebbe modificare
        # le API connections di altri tenant indovinando l'api_id.
        _check_api_connection_ownership(api_id, current_user)
        # Difesa in profondità: impedisci al client di trasferire la connection
        # a un altro gruppo/owner via payload (escalation cross-tenant).
        data.pop("group_id", None)
        data.pop("created_by", None)
        result = _api_connection_service.update_api_connection(api_id, data)
        if not result:
            raise HTTPException(404, f"API connection '{api_id}' non trovata")
        if result is not None:
            result["can_edit"] = compute_can_edit(result, current_user)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore aggiornamento API connection {api_id}: {e}", exc_info=True)
        raise HTTPException(500, str(e))


@api_connections_router.delete("/api-connections/{api_id}")
def delete_api_connection(api_id: str, current_user: CurrentUser = Depends(require_admin)):
    """Elimina una API connection, con check se è in uso in qualche canvas. Solo admin/superadmin."""
    # Ownership check PRIMA di qualunque altra cosa: 404/403 se non sei
    # autorizzato. Senza questo, l'attacker potrebbe almeno sondare quali
    # api_id esistono via il messaggio "in_use_by" o "non trovata".
    _check_api_connection_ownership(api_id, current_user)

    # Controlla se l'api_id è referenziata in canvas di progetti arricchisci
    in_use_by = []
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
                canvas = _project_service.get_project_canvas(proj_id)
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
                        in_use_by.append({'project_id': proj_id, 'project_name': proj_name})
                        break
    except Exception as e:
        logger.warning(f"Errore check canvas per delete {api_id}: {e}")

    if in_use_by:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"La connessione è usata in {len(in_use_by)} progetto/i. Rimuoverla dal canvas prima di eliminarla.",
                "in_use_by": in_use_by
            }
        )

    if not _api_connection_service.delete_api_connection(api_id):
        raise HTTPException(404, f"API connection '{api_id}' non trovata")
    return {"status": "deleted", "api_id": api_id}


@api_connections_router.post("/api-connections/test")
def test_api_config(
    data: dict,
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
            "custom_headers": data.get("custom_headers", {})
        }
        test_params = data.get("test_params", {})
        return _api_connection_service.test_api_connection(config=config, test_params=test_params)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore test API config: {e}", exc_info=True)
        raise HTTPException(500, str(e))


@api_connections_router.post("/api-connections/{api_id}/test")
def test_api_connection(
    api_id: str,
    body: dict = None,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Testa una API connection salvata per ID.

    Endpoint sensibile: triggera una chiamata HTTP REALE verso l'API
    esterna usando le credenziali decifrate del tenant. Solo chi vede
    la risorsa (owned o condivisa via allowed_groups) può testarla.
    """
    try:
        _check_api_connection_visible(api_id, current_user)
        test_params = (body or {}).get("test_params", {})
        return _api_connection_service.test_api_connection(api_id=api_id, test_params=test_params)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore test API connection {api_id}: {e}", exc_info=True)
        raise HTTPException(500, str(e))


# =============================================================================
# ENDPOINT: TEMPLATES API PRECONFIGURATI
# =============================================================================

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
        raise HTTPException(500, str(e))


# =============================================================================
# ENDPOINT: EXECUTABLES
# =============================================================================

# Cap massimo per il timeout di test (secondi). Evita chiamate che bloccano un worker a lungo.
_TEST_EXEC_TIMEOUT_MAX = 60

# Variabili d'ambiente che NON devono essere passate al child process: alterano il
# loader/runtime e possono essere usate per code injection (LD_PRELOAD), bypass di
# sandbox (PATH override), ecc. Vengono filtrate sia dal merge utente sia ereditate.
_BLOCKED_ENV_VARS = {
    "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME",
    "PATH",  # forziamo l'uso del PATH del processo padre senza override utente
}


def _validate_sandboxed_path(raw_path: str, label: str) -> Path:
    """Risolve `raw_path` e verifica che sia dentro DATA_DIR. Solleva HTTPException 400 altrimenti."""
    if not raw_path:
        raise HTTPException(400, f"{label} non specificato")
    try:
        resolved = Path(raw_path).expanduser().resolve(strict=False)
    except Exception:
        raise HTTPException(400, f"{label} non valido")
    data_root = settings.DATA_DIR.resolve()
    try:
        resolved.relative_to(data_root)
    except ValueError:
        raise HTTPException(
            403,
            f"{label} fuori dalla sandbox: deve essere all'interno di {data_root}"
        )
    return resolved


@executables_router.post("/test")
def test_executable(
    data: dict,
    current_user: CurrentUser = Depends(require_admin),
):
    """
    Testa un eseguibile locale con input di prova.
    Usato dallo step Test del wizard ExecutableConfigModal.

    SECURITY:
      - Solo admin/superadmin (RCE potential)
      - exec_path e working_dir devono risolvere dentro settings.DATA_DIR
      - env vars utente filtrate per bloccare LD_PRELOAD/PATH/PYTHONPATH ecc.
      - timeout massimo 60s
    """
    import subprocess
    import time

    exec_config = data.get("executable", {})
    exec_type = exec_config.get("type", "python")
    exec_path_raw = exec_config.get("path", "")
    working_dir_raw = exec_config.get("working_dir") or None
    input_mode = exec_config.get("input_mode", "json_arg")
    output_mode = exec_config.get("output_mode", "json_stdout")
    timeout = min(int(exec_config.get("timeout", 30) or 30), _TEST_EXEC_TIMEOUT_MAX)
    env_vars = exec_config.get("env_vars", {}) or {}
    test_input = data.get("test_input", "{}")

    # Builtin module test (import-only, no subprocess).
    # SECURITY: solo moduli registrati in BUILTIN_REGISTRY sono ammessi.
    # Senza whitelist, un admin potrebbe usare importlib.import_module su
    # qualsiasi modulo Python (os, subprocess, ecc.) — l'IMPORT stesso esegue
    # il code del modulo (side effects), oltre a permettere di scoprire
    # dipendenze installate sul server.
    builtin_module = exec_config.get("module")
    if builtin_module:
        from app.core.arricchisci.executable_runner import BUILTIN_REGISTRY
        # Whitelist: solo i moduli dichiarati nel registry sono importabili
        allowed_modules = {cfg.get("module") for cfg in BUILTIN_REGISTRY.values() if cfg.get("module")}
        if builtin_module not in allowed_modules:
            return {
                "success": False,
                "error": (
                    f"Modulo '{builtin_module}' non in whitelist. "
                    f"Moduli ammessi: {sorted(allowed_modules)}"
                )
            }
        try:
            import importlib
            mod = importlib.import_module(builtin_module)
            func_name = exec_config.get("function", "process")
            if not hasattr(mod, func_name):
                return {"success": False, "error": f"Funzione '{func_name}' non trovata in {builtin_module}"}
            return {
                "success": True,
                "output": f"Modulo {builtin_module}.{func_name} trovato e importabile",
                "elapsed": "0.0s"
            }
        except ImportError as e:
            return {"success": False, "error": f"Modulo non trovato: {e}"}

    # Sandbox: exec_path DEVE essere dentro DATA_DIR (no /bin/bash, /etc/..., ecc.)
    exec_path_resolved = _validate_sandboxed_path(exec_path_raw, "Percorso eseguibile")
    if not exec_path_resolved.exists():
        return {"success": False, "error": f"Eseguibile non trovato: {exec_path_resolved}"}
    if not exec_path_resolved.is_file():
        return {"success": False, "error": f"Il percorso non è un file: {exec_path_resolved}"}

    # Working dir opzionale: se passato, deve anch'esso essere nella sandbox
    if working_dir_raw:
        working_dir_resolved = _validate_sandboxed_path(working_dir_raw, "Working directory")
        if not working_dir_resolved.is_dir():
            return {"success": False, "error": f"Working directory non valida: {working_dir_resolved}"}
        working_dir = str(working_dir_resolved)
    else:
        working_dir = None

    # Costruisci comando con il path validato (no più stringa raw)
    cmd_map = {'python': 'python', 'shell': 'bash', 'node': 'node', 'executable': ''}
    cmd_prefix = cmd_map.get(exec_type, '')

    cmd = []
    if cmd_prefix:
        cmd.append(cmd_prefix)
    cmd.append(str(exec_path_resolved))

    stdin_data = None
    if input_mode == 'json_arg':
        cmd.append(test_input)
    elif input_mode == 'stdin':
        stdin_data = test_input.encode('utf-8')
    elif input_mode == 'args':
        try:
            import json as json_mod
            params = json_mod.loads(test_input)
            for k, v in params.items():
                cmd.extend([f'--{k}', str(v)])
        except Exception:
            pass

    # Env: parti dall'env del processo padre PRIVATO delle variabili pericolose,
    # poi aggiungi quelle utente FILTRATE (anche qui niente LD_PRELOAD ecc.)
    import os
    safe_env = {k: v for k, v in os.environ.items() if k not in _BLOCKED_ENV_VARS}
    user_env_filtered = {k: str(v) for k, v in env_vars.items() if k not in _BLOCKED_ENV_VARS}
    env = {**safe_env, **user_env_filtered}

    logger.info(
        f"test_executable da user={current_user.user_id} "
        f"path={exec_path_resolved} type={exec_type}",
        op="test_executable"
    )

    try:
        start = time.time()
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            cwd=working_dir,
            input=stdin_data,
            env=env
        )
        elapsed = time.time() - start

        stdout = proc.stdout.decode('utf-8', errors='replace')[:5000]
        stderr = proc.stderr.decode('utf-8', errors='replace')[:2000]

        if proc.returncode != 0:
            return {
                "success": False,
                "error": f"Exit code {proc.returncode}: {stderr}",
                "elapsed": f"{elapsed:.1f}s"
            }

        return {
            "success": True,
            "output": stdout,
            "elapsed": f"{elapsed:.1f}s"
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Timeout superato ({timeout}s)"}
    except FileNotFoundError:
        return {"success": False, "error": f"Eseguibile non trovato: {exec_path_resolved}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# ENDPOINT: ESECUZIONE ARRICCHIMENTO (sincrono/asincrono)
# =============================================================================

enrich_router = APIRouter(prefix="/projects", tags=["arricchisci"])


class EnrichAsyncRequest(BaseModel):
    format: str = "csv"
    force_recalculate: bool = False


@enrich_router.post("/{project_id}/enrich-async")
def execute_enrichment_async(
    project_id: str,
    request: EnrichAsyncRequest = None,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """
    Esegue l'arricchimento in modo ASINCRONO con Celery.

    Parametri body (opzionali):
        format: formato output ('csv', 'xlsx', 'geojson', ...) — default 'csv'
        force_recalculate: ignora cache e ricalcola tutto — default False
    """
    require_service(_project_service, "ProjectService")
    require_service(_api_connection_service, "ApiConnectionService")
    # Auth + ownership: stesso ragionamento di /enrich sincrono
    _check_project_ownership(project_id, current_user)
    from app.services.task_service import task_service

    # Valori di default se body non fornito
    if request is None:
        request = EnrichAsyncRequest()

    try:
        metadata = _project_service.get_project_metadata(project_id)
        if not metadata:
            raise HTTPException(404, f"Progetto '{project_id}' non trovato")
        if metadata.project_type != "arricchisci":
            raise HTTPException(400, f"Progetto '{project_id}' non è di tipo 'arricchisci'")

        canvas = _project_service.get_project_canvas(project_id)
        if not canvas:
            raise HTTPException(400, "Canvas non trovato")

        # Formato: usa il valore dal body se specificato,
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
            except Exception:
                pass  # fallback a csv

        task_info = task_service.create_enrichment_task(
            project_id=project_id,
            canvas=canvas.model_dump(),
            output_format=output_format,
            force_recalculate=request.force_recalculate
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
        raise HTTPException(500, str(e))


# =============================================================================
# HELPER
# =============================================================================

def _load_data(project_id: str, file_source_id: str = None):
    """Carica dati da progetto o file source.
    
    Se file_source_id è specificato, usa SOLO quello — non fa fallback silenzioso
    al progetto se il file source fallisce, per evitare di restituire dati inconsistenti.
    """
    import pandas as pd
    
    if file_source_id:
        try:
            from app.services.file_service import file_source_service
            df, _ = file_source_service.load_dataframe(file_source_id)
            return df
        except Exception as e:
            logger.error(f"[GESTITO] Errore caricamento file source {file_source_id}: {e}")
            # NON fare fallback al progetto: l'utente ha specificato esplicitamente
            # un file source — restituire dati diversi sarebbe un bug silenzioso.
            raise HTTPException(
                status_code=400,
                detail=f"Impossibile caricare il file source '{file_source_id}': {e}"
            )
    
    if project_id:
        return _load_project_output(project_id)
    
    return None


def _load_project_output(project_id: str):
    """Carica l'ultimo output di un progetto dal DB SQLite (stesso pattern ESTRAI/CARICA)."""
    from app.utils.result_store import get_result_as_tempfile
    from app.utils.dataframe import load_dataframe_from_file
    import os

    tmp = get_result_as_tempfile(project_id)
    if not tmp:
        return None

    try:
        return load_dataframe_from_file(tmp)
    except Exception as e:
        logger.error(f"[CRASH] Errore caricamento output {project_id}: {e}", exc_info=True)
        return None
    finally:
        try:
            if tmp and os.path.exists(tmp):
                os.unlink(str(tmp))
        except Exception:
            pass


def _format_time(seconds: float) -> str:
    """Formatta tempo in formato leggibile."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    else:
        return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"