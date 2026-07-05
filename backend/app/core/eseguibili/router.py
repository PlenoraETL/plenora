"""
Router FastAPI per gli eseguibili.

Endpoint:
"""

import json as _json

from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, BackgroundTasks
from fastapi.responses import StreamingResponse
from typing import List, Mapping, Optional

from app.core.auth.dependencies import (
    get_current_user,
    require_admin,
)
from app.shared.contracts.auth import CurrentUser
from app.core.auth.ownership import (
    assert_resource_visible,
    assert_can_modify,
    compute_can_edit,
)
from app.config import settings
from app.core.files.service import file_source_service
from app.core.io.local_cleanup import rmtree_safe
from app.utils.logger import get_logger
from app.utils.mime_validator import MimeMismatchError, validate_no_executable
from app.observability.audit_action import audit_action
from app.core.auth.visibility import visible_resource_ids
from app.shared.error_sanitizer import sanitize_http_detail
from app.core.security.upload_guards import enforce_upload_size_limit
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http

from . import db as eseguibili_db
from . import output_store as _output_store
from . import service as eseguibili_service
from .models import (
    EseguibileCreate, EseguibileUpdate, EseguibileSummary, EseguibileDetails,
    ExecuteRequest, AnalyzeUploadResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/eseguibili", tags=["eseguibili"])


def _check_group_access(eseguibile: object, user: CurrentUser) -> None:
    """Hierarchical ownership (operazioni di SCRITTURA): solo admin del gruppo
    proprietario, e solo se non creato da un superadmin."""
    assert_can_modify(eseguibile, user, "Eseguibile")


def _check_visible(eseguibile: object, user: CurrentUser) -> None:
    """Visibility check (operazioni di LETTURA: get / analyze / execute):
    owned + condivisi via allowed_groups."""
    assert_resource_visible(eseguibile, "eseguibili", user, "Eseguibile")


def _summary_response(
    record: eseguibili_db.EseguibileSummaryRow,
    user: CurrentUser,
) -> Mapping[str, object]:
    """Serialize summary row at the API edge."""
    data = {
        "id": record.id,
        "name": record.name,
        "description": record.description,
        "default_output_format": record.default_output_format,
        "default_output_name": record.default_output_name,
        "icon": record.icon,
        "source_canvas_id": record.source_canvas_id,
        "group_id": record.group_id,
        "created_by": record.created_by,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "expected_columns_count": record.expected_columns_count,
    }
    data["can_edit"] = compute_can_edit(record, user)
    return data


def _details_response(
    record: eseguibili_db.EseguibileRow,
    user: CurrentUser,
) -> Mapping[str, object]:
    """Serialize detail row at the API edge."""
    data = _to_details(record)
    data["can_edit"] = compute_can_edit(record, user)
    return data


# LIST

@router.get("", response_model=List[EseguibileSummary])
def list_eseguibili(current_user: CurrentUser = Depends(get_current_user)):
    """Lista eseguibili visibili: owned dal gruppo + condivisi via allowed_groups.
    Ogni record include `can_edit` calcolato per l'utente corrente."""
    is_super = current_user.role == "superadmin"

    rows = eseguibili_db.list_eseguibili(group_id=None)

    if not is_super:
        visible_ids = visible_resource_ids(
            'eseguibili',
            current_user.group_id,
            is_superadmin=False,
        )
        rows = [r for r in rows if r.id in visible_ids]

    return [_summary_response(r, current_user) for r in rows]


# CREATE (solo admin

@router.post("", response_model=EseguibileDetails, status_code=201)
@audit_action(action="eseguibile.create")
def create_eseguibile(
    payload: EseguibileCreate,
    current_user: CurrentUser = Depends(require_admin)
):
    """Crea un eseguibile clonando il canvas di un progetto arricchisci esistente.

    Solo gli admin del gruppo possono pubblicare. Il progetto sorgente deve appartenere
    allo stesso gruppo dell'admin (o qualsiasi gruppo se superadmin).
    """
    try:
        record = eseguibili_service.create_from_project(
            source_project_id=payload.source_project_id,
            name=payload.name,
            group_id=current_user.group_id,
            created_by=current_user.user_id,
            description=payload.description or "",
            default_output_format=payload.default_output_format,
            default_output_name=payload.default_output_name,
            icon=payload.icon,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[CRASH] Errore creazione eseguibile: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")

    return _details_response(record, current_user)


# GET ONE

@router.get("/{eseguibile_id}", response_model=EseguibileDetails)
def get_eseguibile(
    eseguibile_id: str,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Dettaglio eseguibile. Read-only su risorsa visibile (owned + shared)."""
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    _check_visible(record, current_user)
    return _details_response(record, current_user)


# UPDATE (solo admin

@router.put("/{eseguibile_id}", response_model=EseguibileDetails)
@audit_action(action="eseguibile.update", target_param="eseguibile_id")
def update_eseguibile(
    eseguibile_id: str,
    payload: EseguibileUpdate,
    current_user: CurrentUser = Depends(require_admin)
):
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    _check_group_access(record, current_user)

    updated = eseguibili_db.update_eseguibile(
        eseguibile_id,
        name=payload.name,
        description=payload.description,
        default_output_format=payload.default_output_format,
        default_output_name=payload.default_output_name,
        icon=payload.icon,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Eseguibile non trovato")
    return _details_response(updated, current_user)


# DELETE (solo admin

@router.delete("/{eseguibile_id}", status_code=204)
@audit_action(action="eseguibile.delete", target_param="eseguibile_id")
def delete_eseguibile(
    eseguibile_id: str,
    current_user: CurrentUser = Depends(require_admin)
):
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    _check_group_access(record, current_user)

    eseguibili_db.delete_eseguibile(eseguibile_id)
    # DELETE risponde 204 No Content, body vuoto.


# ANALYZE UPLOAD

_LOCAL_UPLOAD_FALLBACK_ENVS = {"dev", "development", "local", "test", "testing"}


def _local_upload_meta_fallback_allowed() -> bool:
    env_name = str(
        settings.get("ENVIRONMENT", settings.get("ENV", "development"))
        or "development"
    ).lower()
    return env_name in _LOCAL_UPLOAD_FALLBACK_ENVS or bool(
        settings.get("PYTEST_CURRENT_TEST")
    )

# Upload metadata store.
# il dict non era condiviso tra processi → analyze e execute potevano
# Source of truth: file_sources DB metadata; local sidecar remains readable
# only in dev/test for stale pre-migration uploads.

def _upload_meta_path(file_id: str) -> Path:
    """Percorso del file metadata per un upload."""
    tmp_dir = settings.DATA_DIR / "temp" / "eseguibili_uploads"
    # Sanitizza file_id per evitare path traversal
    safe_id = "".join(c for c in file_id if c.isalnum() or c in "-_")
    return tmp_dir / f"{safe_id}.meta.json"


def _load_upload_meta(file_id: str) -> Optional[Mapping[str, object]]:
    """Carica metadata upload durable dal DB; legacy local solo dev/test."""
    meta = eseguibili_service.load_upload_metadata(file_id)
    if meta is not None:
        return meta
    if not _local_upload_meta_fallback_allowed():
        return None

    path = _upload_meta_path(file_id)
    if not path.exists():
        return None
    try:
        return _json.loads(path.read_text(encoding="utf-8"))
    except Exception as _meta_err:
        logger.warning(
            f"_load_upload_meta: metadata file_id={file_id} presente ma non "
            f"decodificabile ({type(_meta_err).__name__}: {_meta_err}); "
            f"ritorno None -> analyze/execute vedranno 'upload non trovato'"
        )
        return None


def _delete_upload_meta(file_id: str) -> None:
    """Elimina l'upload temporaneo durable dopo l'uso."""
    try:
        if file_source_service.delete_file_source(file_id):
            return
    except Exception as _delete_err:
        logger.debug(
            f"_delete_upload_meta: delete file_source best-effort fallito "
            f"per file_id={file_id}: {_delete_err}"
        )

    if not _local_upload_meta_fallback_allowed():
        return

    path = _upload_meta_path(file_id)
    try:
        if path.exists():
            rmtree_safe(path, logger=logger)
    except Exception as _unlink_err:
        logger.debug(
            f"_delete_upload_meta best-effort fallito per file_id={file_id}: "
            f"{_unlink_err}"
        )


# ─ Task ownership metadata ──────────────────────────────────────────────
# Tracking task_id → (eseguibile_id, user_id) per autorizzare il download
# Scritto al momento dell'execute, letto al download, cancellato al cleanup
def _task_owner_path(task_id: str) -> Path:
    return _output_store.task_owner_path(
        task_id,
        outputs_dir=settings.ESEGUIBILI_OUTPUTS_DIR,
    )


def _save_task_owner(task_id: str, eseguibile_id: str, user_id: str) -> None:
    _output_store.save_task_owner(
        task_id,
        eseguibile_id,
        user_id,
        outputs_dir=settings.ESEGUIBILI_OUTPUTS_DIR,
    )


def _load_task_owner(task_id: str) -> Optional[Mapping[str, object]]:
    return _output_store.load_task_owner(
        task_id,
        outputs_dir=settings.ESEGUIBILI_OUTPUTS_DIR,
    )


@router.post("/{eseguibile_id}/analyze-upload", response_model=AnalyzeUploadResponse)
@audit_action(action="eseguibile.analyze_upload", target_param="eseguibile_id")
async def analyze_upload(
    eseguibile_id: str,
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Upload file utente, parse e restituisce colonne rilevate + auto-mapping.

    Il file viene salvato in una cartella temp. Il file_id ritornato viene poi usato
    nel successivo POST /execute. I file temp vengono eliminati dopo l'esecuzione.
    Read-only su eseguibile visibile (owned + shared).
    """
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    _check_visible(record, current_user)

    # Size cap pre-read (helper centralizzato, vedi `app.core.security.upload_guards`
    # Coerente con `files/router.py`: rifiuta con 413 prima di leggere il
    # contenuto in RAM, evitando DoS interno credenziato via upload N GB
    enforce_upload_size_limit(file)

    try:
        content = await file.read()
        # Block-only mode: il file viene salvato su disk e parsato dal
        # workflow eseguibile. Bloccare eseguibili reali e' la difesa
        # primaria (un binary su disk poi eseguito sarebbe RCE). Allowlist
        # stretta romperebbe i formati custom degli eseguibili (.dxf, .geojson
        try:
            validate_no_executable(
                content=content, filename=file.filename or "upload",
            )
        except MimeMismatchError as e:
            logger.warning(
                f"Upload eseguibile analyze rifiutato (MIME): "
                f"eseguibile={eseguibile_id} user={current_user.username}: {e}"
            )
            # 6.2: sanitize detail prima di esporlo al client
            raise HTTPException(
                status_code=400, detail=sanitize_http_detail(str(e)),
            )
        try:
            result = eseguibili_service.analyze_file_upload(
                eseguibile=record,
                file_content=content,
                filename=file.filename,
            )
        except ValueError as e:
            # 6.2: sanitize detail (analyze_file_upload puo'
            # includere output pyproj/gdal sensibile)
            raise HTTPException(
                status_code=400, detail=sanitize_http_detail(str(e)),
            )

        durable_file_id = eseguibili_service.persist_upload_file_source(
            eseguibile=record,
            file_content=content,
            filename=Path((file.filename or "upload.bin").replace("\\", "/")).name,
            user_id=current_user.user_id,
            group_id=current_user.group_id,
        )
        result = result.model_copy(
            update={"file_id": durable_file_id, "file_path": ""}
        )

        return result.to_api_dict()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore analyze upload: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


# EXECUTE

@router.post("/{eseguibile_id}/execute")
@audit_action(action="eseguibile.execute", target_param="eseguibile_id")
def execute_eseguibile(
    eseguibile_id: str,
    payload: ExecuteRequest,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Esegue un eseguibile: rinomina colonne secondo il mapping, clona il canvas,
    sostituisce la sorgente con il file caricato e lancia il task di enrichment.
    Read-only sull'eseguibile (anche viewer e gruppi destinatari di share possono eseguire).
    """
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    _check_visible(record, current_user)

    # Recupera metadata upload da disco (multi-worker safe
    upload = _load_upload_meta(payload.file_id)
    if not upload:
        raise HTTPException(
            status_code=400,
            detail="File upload non trovato o scaduto. Ricarica il file."
        )
    if upload["eseguibile_id"] != eseguibile_id:
        raise HTTPException(status_code=400, detail="File upload non corrisponde all'eseguibile")
    # Authz: il file_id deve appartenere allo stesso utente che l'ha caricato
    # Senza questo check, se un attacker conosce un file_id valido di un altro
    # utente puo' eseguire l'eseguibile con i suoi dati (data exfil / abuso
    upload_user = upload.get("user_id")
    if upload_user and upload_user != current_user.user_id:
        raise HTTPException(
            status_code=403,
            detail="File upload appartiene a un altro utente"
        )

    try:
        execute_kwargs = {
            "eseguibile": record,
            "column_mapping": [m.model_dump() for m in payload.column_mapping],
            "output_format": payload.output_format,
            "output_name": payload.output_name,
        }
        if upload.get("file_source_id"):
            execute_kwargs["file_source_id"] = str(upload["file_source_id"])
            execute_kwargs["upload_group_id"] = str(
                upload.get("group_id") or current_user.group_id
            )
        elif _local_upload_meta_fallback_allowed() and upload.get("path"):
            execute_kwargs["file_path"] = str(upload["path"])
        else:
            raise ValueError("Upload non idratabile cross-host")

        result = eseguibili_service.execute(
            **execute_kwargs,
        )
        # Persist ownership del task per il download endpoint: senza
        # questo file un attacker che indovina un task_id valido puo'
        # scaricare l'output di un'esecuzione non sua
        try:
            _task_id = None
            _task_id = result.task_id
            if _task_id:
                _save_task_owner(_task_id, eseguibile_id, current_user.user_id)
        except Exception as _me:
            logger.warning(f"save task owner meta failed: {_me}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[CRASH] Errore execute eseguibile: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")
    finally:
        # Cleanup: elimina il file_source upload temporaneo + metadata.
        if _local_upload_meta_fallback_allowed() and upload.get("path"):
            try:
                rmtree_safe(str(upload["path"]), logger=logger)
            except Exception as _unlink_err:
                logger.debug(
                    f"execute_eseguibile: cleanup tmp upload legacy "
                    f"best-effort fallito per {upload.get('path')}: "
                    f"{_unlink_err}"
                )
        _delete_upload_meta(payload.file_id)

    return result.to_api_dict()


# DOWNLOAD OUTPUT

@router.get("/{eseguibile_id}/download/{task_id}")
def download_eseguibile_output(
    eseguibile_id: str,
    task_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Scarica il file di output prodotto da un'esecuzione dell'eseguibile.

    Il file vive in ESEGUIBILI_OUTPUTS_DIR/{task_id}/ ed e' stato creato dal task
    Celery (execute_enrichment_task con is_eseguibile=True). Dopo l'invio il file
    viene rimosso. Un celery beat periodico pulisce eventuali file orfani (> 2h).
    """
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    _check_visible(record, current_user)

    # Sanitizza task_id per evitare path traversal
    safe_task_id = "".join(c for c in task_id if c.isalnum() or c in "-_")
    if not safe_task_id or safe_task_id != task_id:
        raise HTTPException(status_code=400, detail="task_id non valido")

    # Authz: verifica che il task_id sia stato effettivamente generato da
    # QUESTO eseguibile e da QUESTO utente. Senza questo check, chiunque
    # veda un eseguibile condiviso e conosca/indovini un task_id valido
    # puo' scaricare l'output di un'esecuzione altrui
    owner_meta = _load_task_owner(safe_task_id)
    if owner_meta is None:
        # Metadata assente: task precedente al tracking owner o cleanup gia' fatto.
        # Fail-closed: non consentire l'accesso se non siamo sicuri
        if current_user.role != "superadmin":
            raise HTTPException(
                status_code=404,
                detail="Output non trovato o scaduto (ownership metadata assente)"
            )
    else:
        if owner_meta.get("eseguibile_id") != eseguibile_id:
            raise HTTPException(status_code=404, detail="Task non appartiene a questo eseguibile")
        if owner_meta.get("user_id") != current_user.user_id and current_user.role != "superadmin":
            raise HTTPException(
                status_code=403,
                detail="Output non accessibile (task eseguito da un altro utente)"
            )

    # Lookup dell'output su object storage (S3 via cloud_backends
    # Multi-host safe: backend e worker possono essere su macchine diverse
    try:
        keys = _output_store.find_output_keys(safe_task_id)
    except Exception as e:
        logger.warning(f"Lookup storage eseguibili fallito: {e}")
        keys = []

    if not keys:
        raise HTTPException(
            status_code=404,
            detail="Output non trovato. Il file potrebbe essere scaduto o il task non ancora completato.",
        )

    # Tipicamente 1 file per task (convention
    key = keys[0]
    filename = key.rsplit("/", 1)[-1]

    try:
        stream = _output_store.open_stream(key)
    except Exception as e:
        logger.error(f"[CRASH] open_stream eseguibile {key}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore apertura stream output")

    def _cleanup_after_send():
        try:
            _output_store.delete_task_outputs(safe_task_id)
        except Exception as e:
            logger.warning(f"Cleanup storage post-download fallito per {safe_task_id}: {e}")

    background_tasks.add_task(_cleanup_after_send)

    size = stream.get("size") or 0
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    if size:
        headers["Content-Length"] = str(size)

    return StreamingResponse(
        stream["body"],
        media_type="application/octet-stream",
        headers=headers,
    )


# HELPER

def _detect_file_based_output(canvas: dict) -> tuple:
    """Ispeziona il canvas per determinare se il flusso finisce con un
    file-processor (connection_type == `rest_file`).

    Strategia:
    1. Prova il pattern canonico: trova il `result` node e guarda il suo
       predecessore (via edge in ingresso).
    2. Fallback: scan dei nodi api_enrich che non hanno outgoing edges
       (sink del DAG) → primo match.

    Ritorna `(is_file_based: bool, ext: Optional[str])` dove `ext` è
    l'estensione SENZA il punto (es. `"zip"`), presa da
    `node.response.output_extension` o `node.data.response.output_extension`.
    Default `"zip"` se il nodo è file-based ma non specifica un'estensione.
    """
    nodes = canvas.get("nodes") or []
    edges = canvas.get("edges") or []

    candidate = None
    result_node = next((n for n in nodes if n.get("type") == "result"), None)
    if result_node:
        in_edge = next(
            (e for e in edges if e.get("target") == result_node.get("id")),
            None,
        )
        if in_edge:
            src_id = in_edge.get("source")
            candidate = next((n for n in nodes if n.get("id") == src_id), None)

    if not candidate:
        # Fallback: api_enrich sink (nessun outgoing edge
        has_outgoing = {e.get("source") for e in edges}
        sinks = [
            n for n in nodes
            if n.get("type") == "api_enrich" and n.get("id") not in has_outgoing
        ]
        candidate = sinks[0] if sinks else None

    if not candidate or candidate.get("type") != "api_enrich":
        return False, None

    data = candidate.get("data") or {}
    ctype = candidate.get("connection_type") or data.get("connection_type")
    if ctype != "rest_file":
        return False, None

    response = candidate.get("response") or data.get("response") or {}
    raw_ext = response.get("output_extension") if isinstance(response, dict) else None
    if isinstance(raw_ext, str) and raw_ext.strip():
        ext = raw_ext.lstrip(".").strip()
    else:
        ext = "zip"
    return True, ext


def _to_details(record: eseguibili_db.EseguibileRow) -> Mapping[str, object]:
    """Converte un record DB in EseguibileDetails response."""
    canvas = record.canvas
    is_file_based, inferred_ext = _detect_file_based_output(canvas)
    return {
        "id": record.id,
        "name": record.name,
        "description": record.description or "",
        "expected_columns": record.expected_columns,
        "default_output_format": record.default_output_format or "xlsx",
        "default_output_name": record.default_output_name,
        "icon": record.icon,
        "source_canvas_id": record.source_canvas_id,
        "group_id": record.group_id,
        "created_by": record.created_by,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "can_edit": None,
        "output_is_file_based": is_file_based,
        "inferred_output_extension": inferred_ext,
    }
