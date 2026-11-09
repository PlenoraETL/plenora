"""
Router FastAPI per gli eseguibili.

Endpoint:
"""

import json as _json

from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Query
from typing import Annotated, List, Mapping, NamedTuple, Optional

from app.core.auth.dependencies import (
    get_current_user,
    require_admin,
)
from app.shared.contracts.auth import CurrentUser
from app.shared.contracts.payloads import JsonObject
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
from app.core.io.bounded_streams import read_prefix
from app.core.orchestrator.task_service import task_service as _task_service
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http
from app.shared.execution.runtime_identity import RuntimeIdentity, RuntimeIdentityError

from . import db as eseguibili_db
from . import output_store as _output_store
from .download_response import build_consume_once_response
from . import service as eseguibili_service
from . import preflight as eseguibili_preflight
from .models import (
    EseguibileCreate, EseguibileUpdate, EseguibileSummary, EseguibileDetails,
    ExecuteRequest, AnalyzeUploadResponse, ExecutableManifest,
    ExecutableRunRequest,
    ExecutablePreflightRequest,
    EseguibileVersionSummary,
    EseguibileSourceStatus,
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
        "project_type": record.project_type,
        "active_version": record.active_version,
        "inputs_count": record.inputs_count,
        "has_manifest": record.has_manifest,
        # N1: solo per chi puo' ripubblicare (l'esecutore non vede nulla in piu').
        "source_changed": record.source_changed if user.role in ("admin", "superadmin") else None,
        "binding_warnings": eseguibili_service.binding_warnings(
            list(record.bindings), {"nodes": list(record.binding_target_nodes)},
        ),
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
def list_eseguibili(
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Lista eseguibili visibili: owned dal gruppo + condivisi via allowed_groups.
    Ogni record include `can_edit` calcolato per l'utente corrente."""
    is_super = current_user.role == "superadmin"

    if is_super:
        rows = eseguibili_db.list_eseguibili(
            group_id=None, limit=limit, offset=offset,
        )
    else:
        visible_ids = visible_resource_ids(
            'eseguibili',
            current_user.group_id,
            is_superadmin=False,
        )
        rows = eseguibili_db.list_eseguibili(
            group_id=None,
            resource_ids=visible_ids,
            limit=limit,
            offset=offset,
        )

    return [_summary_response(r, current_user) for r in rows]


# CREATE (solo admin

@router.post("", response_model=EseguibileDetails, status_code=201)
@audit_action(action="eseguibile.create")
def create_eseguibile(
    payload: EseguibileCreate,
    current_user: CurrentUser = Depends(require_admin)
):
    """Pubblica un progetto Estrai, Manipola o Arricchisci come eseguibile.

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


# N1 — l'Eseguibile corrisponde ancora al suo progetto? (solo admin: chi puo'
# ripubblicare; il dettaglio letto dall'esecutore resta com'e')

@router.get("/by-source/{project_id}", response_model=List[EseguibileSourceStatus])
def list_source_status(
    project_id: str,
    current_user: CurrentUser = Depends(require_admin),
) -> list[JsonObject]:
    """Gli Eseguibili pubblicati dal progetto, con lo stato rispetto al canvas
    attuale (avviso nell'editor del progetto sorgente). Solo quelli del gruppo
    dell'admin (tutti per il superadmin)."""
    group_id = None if current_user.role == "superadmin" else current_user.group_id
    return eseguibili_service.source_status_for_project(project_id, group_id=group_id)


@router.get("/{eseguibile_id}/source-status", response_model=EseguibileSourceStatus)
def get_source_status(
    eseguibile_id: str,
    current_user: CurrentUser = Depends(require_admin),
) -> JsonObject:
    """Stato rispetto al progetto sorgente e anteprima della ripubblicazione."""
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    _check_group_access(record, current_user)
    return eseguibili_service.source_status(record)


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


@router.get(
    "/{eseguibile_id}/versions",
    response_model=List[EseguibileVersionSummary],
)
def list_eseguibile_versions(
    eseguibile_id: str,
    current_user: CurrentUser = Depends(require_admin),
):
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    _check_group_access(record, current_user)
    return [
        {
            "id": version.id,
            "version_number": version.version_number,
            "schema_version": version.schema_version,
            "project_type": version.project_type,
            "source_project_id": version.source_project_id,
            "source_hash": version.source_hash,
            "status": version.status,
            "created_by": version.created_by,
            "created_at": version.created_at,
            "inputs_count": len(version.manifest.get("inputs") or []),
            "active": version.id == record.active_version_id,
        }
        for version in eseguibili_db.list_eseguibile_versions(eseguibile_id)
    ]


@router.post("/{eseguibile_id}/versions", response_model=EseguibileDetails)
@audit_action(action="eseguibile.republish", target_param="eseguibile_id")
def republish_eseguibile(
    eseguibile_id: str,
    current_user: CurrentUser = Depends(require_admin),
):
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    _check_group_access(record, current_user)
    try:
        updated = eseguibili_service.publish_new_version(
            eseguibile=record,
            created_by=current_user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _details_response(updated, current_user)


@router.post(
    "/{eseguibile_id}/versions/{version_id}/activate",
    response_model=EseguibileDetails,
)
@audit_action(action="eseguibile.version.activate", target_param="eseguibile_id")
def activate_eseguibile_version(
    eseguibile_id: str,
    version_id: str,
    current_user: CurrentUser = Depends(require_admin),
):
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    _check_group_access(record, current_user)
    try:
        eseguibili_db.activate_eseguibile_version(eseguibile_id, version_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    updated = eseguibili_db.get_eseguibile(eseguibile_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Eseguibile non trovato")
    return _details_response(updated, current_user)


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


def _save_task_owner(
    task_id: str, eseguibile_id: str, user_id: str, group_id: str,
) -> None:
    _output_store.save_task_owner(
        task_id,
        eseguibile_id,
        user_id,
        group_id=group_id,
        outputs_dir=settings.ESEGUIBILI_OUTPUTS_DIR,
    )


def _load_task_owner(
    task_id: str, *, group_id: str,
) -> Optional[Mapping[str, object]]:
    return _output_store.load_task_owner(
        task_id,
        group_id=group_id,
        outputs_dir=settings.ESEGUIBILI_OUTPUTS_DIR,
    )


@router.post("/{eseguibile_id}/analyze-upload", response_model=AnalyzeUploadResponse)
@audit_action(action="eseguibile.analyze_upload", target_param="eseguibile_id")
def analyze_upload(
    eseguibile_id: str,
    file: UploadFile = File(...),
    input_key: Annotated[Optional[str], Query(max_length=80)] = None,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Upload file utente, parse e restituisce colonne rilevate + auto-mapping.

    Il file viene salvato in una cartella temp. Il file_id ritornato viene poi usato
    nel successivo POST /execute. I file temp vengono eliminati dopo l'esecuzione.
    Read-only su eseguibile visibile (owned + shared).
    """
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    _check_visible(record, current_user)

    expected_columns_override = None
    input_max_size = None
    if input_key:
        if not eseguibili_db.manifest_present(record.manifest):
            raise HTTPException(status_code=400, detail="Manifest eseguibile assente")
        manifest = ExecutableManifest.model_validate(record.manifest)
        selected_input = next(
            (item for item in manifest.inputs if item.key == input_key), None
        )
        if selected_input is None or selected_input.kind != "file":
            raise HTTPException(status_code=400, detail="Input file non valido")
        expected_columns_override = [
            column.model_dump() for column in selected_input.expected_columns
        ]
        accepted = selected_input.validation.accepted_extensions
        if accepted:
            ext = Path(file.filename or "").suffix.lower().lstrip(".")
            normalized = {item.lower().lstrip(".") for item in accepted}
            if ext not in normalized:
                raise HTTPException(
                    status_code=400,
                    detail=f"Formato non ammesso per {selected_input.label}",
                )
        input_max_size = selected_input.validation.max_size_bytes

    # Size cap pre-read (helper centralizzato, vedi `app.core.security.upload_guards`
    # Coerente con `files/router.py`: rifiuta con 413 prima di leggere il
    # contenuto in RAM, evitando DoS interno credenziato via upload N GB
    enforce_upload_size_limit(file)
    if input_max_size is not None:
        # Verifica la dimensione reale dello stream anche quando il client non
        # valorizza UploadFile.size; il limite dell'input non sostituisce quello
        # globale, quindi entrambi restano applicati.
        enforce_upload_size_limit(file, max_bytes=input_max_size)

    try:
        content_stream = file.file
        # Block-only mode: il file viene salvato su disk e parsato dal
        # workflow eseguibile. Bloccare eseguibili reali e' la difesa
        # primaria (un binary su disk poi eseguito sarebbe RCE). Allowlist
        # stretta romperebbe i formati custom degli eseguibili (.dxf, .geojson
        try:
            validate_no_executable(
                content=read_prefix(content_stream),
                filename=file.filename or "upload",
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
                file_content=content_stream,
                filename=file.filename,
                expected_columns_override=expected_columns_override,
            )
        except ValueError as e:
            # 6.2: sanitize detail (analyze_file_upload puo'
            # includere output pyproj/gdal sensibile)
            raise HTTPException(
                status_code=400, detail=sanitize_http_detail(str(e)),
            )

        durable_file_id = eseguibili_service.persist_upload_file_source(
            eseguibile=record,
            file_content=content_stream,
            filename=Path((file.filename or "upload.bin").replace("\\", "/")).name,
            user_id=current_user.user_id,
            group_id=current_user.group_id,
            input_key=input_key,
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

    reserved_task_id = _task_service.reserve_task_id()
    owner_saved = False
    try:
        _save_task_owner(
            reserved_task_id,
            eseguibile_id,
            current_user.user_id,
            str(record.get("group_id") or current_user.group_id),
        )
        owner_saved = True
        execute_kwargs = {
            "eseguibile": record,
            "column_mapping": [m.model_dump() for m in payload.column_mapping],
            "output_format": payload.output_format,
            "output_name": payload.output_name,
            "task_id": reserved_task_id,
            "user_id": current_user.user_id,
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
    except ValueError as e:
        if owner_saved:
            _output_store.delete_task_owner(
                reserved_task_id,
                group_id=str(record.get("group_id") or current_user.group_id),
            )
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        if owner_saved:
            _output_store.delete_task_owner(
                reserved_task_id,
                group_id=str(record.get("group_id") or current_user.group_id),
            )
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


class _TrustedRunInputs(NamedTuple):
    inputs: Mapping[str, object]
    uploads: list[str]


def _trusted_run_inputs(
    eseguibile_id: str,
    manifest: ExecutableManifest,
    raw_inputs: Mapping[str, object],
    current_user: CurrentUser,
) -> _TrustedRunInputs:
    """Input dell'esecuzione con gli upload verificati (stesso eseguibile, stesso
    utente, stesso input). Restituisce anche gli upload usati: l'avvio li
    consuma, la stima no. Errori = HTTPException 400/403."""

    trusted_inputs = dict(raw_inputs)
    consumed_uploads: list[str] = []
    for definition in manifest.inputs:
        if definition.kind != "file":
            continue
        raw = trusted_inputs.get(definition.key)
        # Un input file obbligatorio senza file (assente, null o lista vuota)
        # si rifiuta qui, prima di materializzare qualunque file temporaneo
        # (punto 5 della correzione dopo la conferma di T2+T4).
        if raw is None or (isinstance(raw, list) and not raw):
            if definition.required:
                raise HTTPException(
                    status_code=400,
                    detail=f"Input obbligatorio mancante: {definition.label}",
                )
            # Facoltativo senza file (assente, null o lista vuota) = nessun
            # file: stessa regola di runtime.resolve_runtime_inputs.
            trusted_inputs.pop(definition.key, None)
            continue
        raw_files = raw if isinstance(raw, list) else [raw]
        if definition.multiple and not isinstance(raw, list):
            raise HTTPException(
                status_code=400,
                detail=f"Payload multi-file non valido per {definition.label}",
            )
        if not definition.multiple and not isinstance(raw, Mapping):
            raise HTTPException(
                status_code=400,
                detail=f"Payload file non valido per {definition.label}",
            )
        trusted_files = []
        for raw_file in raw_files:
            if not isinstance(raw_file, Mapping):
                raise HTTPException(
                    status_code=400,
                    detail=f"Payload file non valido per {definition.label}",
                )
            file_id = str(raw_file.get("file_id") or "")
            upload = _load_upload_meta(file_id) if file_id else None
            if not upload:
                raise HTTPException(
                    status_code=400,
                    detail=f"Upload non trovato o scaduto per {definition.label}",
                )
            if upload.get("eseguibile_id") != eseguibile_id:
                raise HTTPException(status_code=400, detail="Upload di altro eseguibile")
            if upload.get("user_id") not in (None, current_user.user_id):
                raise HTTPException(status_code=403, detail="Upload di un altro utente")
            upload_input_key = upload.get("input_key")
            if upload_input_key not in (None, definition.key):
                raise HTTPException(status_code=400, detail="Upload associato a un altro input")
            trusted_file = dict(raw_file)
            trusted_file["_upload_group_id"] = str(
                upload.get("group_id") or current_user.group_id
            )
            trusted_files.append(trusted_file)
            consumed_uploads.append(file_id)
        trusted_inputs[definition.key] = (
            trusted_files if definition.multiple else trusted_files[0]
        )
    return _TrustedRunInputs(trusted_inputs, consumed_uploads)


def _run_version(record: object, eseguibile_id: str, version_id: Optional[str]) -> tuple[object, ExecutableManifest]:
    version = (
        eseguibili_db.get_eseguibile_version(version_id, eseguibile_id=eseguibile_id)
        if version_id
        else eseguibili_db.get_active_eseguibile_version(eseguibile_id)
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Versione eseguibile non trovata")
    try:
        manifest = ExecutableManifest.model_validate(version.manifest)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=sanitize_http_detail(str(exc)))
    return version, manifest


@router.post("/{eseguibile_id}/preflight")
def preflight_eseguibile_run(
    eseguibile_id: str,
    payload: ExecutablePreflightRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> JsonObject:
    """Stima prima di avviare (Fase 5 T2, BA2): gli stessi input dell'avvio,
    nessuna esecuzione, nessun upload consumato. Risponde come il preflight
    dell'editor: { valid, nodes, preparation_fingerprint, checks }."""

    record = eseguibili_db.get_eseguibile(eseguibile_id)
    _check_visible(record, current_user)
    version, manifest = _run_version(record, eseguibile_id, payload.version_id)
    trusted_inputs, _uploads = _trusted_run_inputs(eseguibile_id, manifest, payload.inputs, current_user)
    try:
        return eseguibili_preflight.preflight_versioned(
            eseguibile=record,
            raw_inputs=trusted_inputs,
            # «Utente che esegue» = chi e' autenticato in questa richiesta
            # (letto dal DB da get_current_user), mai un valore del client.
            identity=RuntimeIdentity.authenticated(current_user),
            version_id=version.id,
        )
    except RuntimeIdentityError as exc:
        raise_typed_http(exc.code, status_code=400, message=exc.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=sanitize_http_detail(str(exc)))


@router.post("/{eseguibile_id}/runs")
@audit_action(action="eseguibile.run", target_param="eseguibile_id")
def create_eseguibile_run(
    eseguibile_id: str,
    payload: ExecutableRunRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Avvia una versione con un insieme arbitrario di input tipizzati."""

    record = eseguibili_db.get_eseguibile(eseguibile_id)
    _check_visible(record, current_user)
    version, manifest = _run_version(record, eseguibile_id, payload.version_id)

    consumed_uploads: list[str] = []
    identity = RuntimeIdentity.authenticated(current_user)
    try:
        trusted_inputs, consumed_uploads = _trusted_run_inputs(
            eseguibile_id, manifest, payload.inputs, current_user,
        )
        # Stima confermata: se la preparazione e' cambiata (versione, input,
        # connettori), niente avvio e gli upload restano per la nuova stima.
        if payload.preparation_fingerprint is not None:
            current = eseguibili_preflight.fingerprint_for(
                eseguibile=record, raw_inputs=trusted_inputs, identity=identity, version_id=version.id,
            )
            if current != payload.preparation_fingerprint:
                consumed_uploads = []
                raise HTTPException(409, detail={
                    "error": error_codes.VALIDATION_ERROR,
                    "reason": "preparation_changed",
                    "message": "Preparazione cambiata: versione, input o connettori diversi dalla stima",
                })

        reserved_task_id = _task_service.reserve_task_id()
        _save_task_owner(
            reserved_task_id,
            eseguibile_id,
            current_user.user_id,
            record.group_id,
        )
        try:
            result = eseguibili_service.execute_versioned(
                eseguibile=record,
                raw_inputs=trusted_inputs,
                user_id=current_user.user_id,
                identity=identity,
                version_id=version.id,
                output_format=payload.output_format,
                output_name=payload.output_name,
                task_id=reserved_task_id,
            )
        except Exception:
            _output_store.delete_task_owner(
                reserved_task_id,
                group_id=record.group_id,
            )
            raise
        return result.to_api_dict()
    except HTTPException:
        raise
    except RuntimeIdentityError as exc:
        consumed_uploads = []
        raise_typed_http(exc.code, status_code=400, message=exc.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=sanitize_http_detail(str(exc)))
    except Exception as exc:
        logger.error(
            f"[CRASH] Errore run eseguibile v2 {eseguibile_id}: {exc}",
            exc_info=True,
        )
        raise_typed_http(
            error_codes.INTERNAL_ERROR,
            status_code=500,
            message="Errore interno del server",
        )
    finally:
        for file_id in consumed_uploads:
            _delete_upload_meta(file_id)


# DOWNLOAD OUTPUT

@router.get("/{eseguibile_id}/download/{task_id}")
def download_eseguibile_output(
    eseguibile_id: str,
    task_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Scarica il file di output prodotto da un'esecuzione dell'eseguibile.

    L'output vive nell'object storage tenant-scoped. Viene eliminato solo dopo
    un trasferimento completo; download interrotti restano disponibili per un
    retry. La lifecycle S3 rimuove gli artifact effimeri rimasti oltre il TTL.
    """
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    _check_visible(record, current_user)

    owner_group_id = str(record.get("group_id") or "")
    if not owner_group_id:
        raise HTTPException(status_code=404, detail="Tenant eseguibile non determinabile")

    # Sanitizza task_id per evitare path traversal
    safe_task_id = "".join(c for c in task_id if c.isalnum() or c in "-_")
    if not safe_task_id or safe_task_id != task_id:
        raise HTTPException(status_code=400, detail="task_id non valido")

    # Authz: verifica che il task_id sia stato effettivamente generato da
    # QUESTO eseguibile e da QUESTO utente. Senza questo check, chiunque
    # veda un eseguibile condiviso e conosca/indovini un task_id valido
    # puo' scaricare l'output di un'esecuzione altrui
    owner_meta = _load_task_owner(safe_task_id, group_id=owner_group_id)
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

    # Solo dopo l'ownership lookup interroghiamo gli output. Un outage del
    # listing non deve essere presentato come risultato scaduto.
    try:
        keys = _output_store.find_output_keys(
            safe_task_id, group_id=owner_group_id,
        )
    except Exception as exc:
        logger.error(f"Lookup storage eseguibili fallito: {exc}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Storage output temporaneamente non disponibile",
        ) from exc

    if not keys:
        raise HTTPException(
            status_code=404,
            detail="Output non trovato. Il file potrebbe essere scaduto o il task non ancora completato.",
        )

    try:
        key = _output_store.select_output_key(keys)
    except RuntimeError as exc:
        logger.error(
            "Selezione output ambigua task_id=%s: %s",
            safe_task_id,
            exc,
        )
        raise HTTPException(
            status_code=409,
            detail="Output ambiguo; cleanup manuale richiesto",
        ) from exc
    return build_consume_once_response(
        task_id=safe_task_id,
        group_id=owner_group_id,
        storage_key=key,
        media_type="application/octet-stream",
        fallback_filename="output.bin",
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
    has_manifest = eseguibili_db.manifest_present(record.manifest)
    raw_manifest = record.manifest if has_manifest else None
    if has_manifest:
        try:
            manifest = ExecutableManifest.model_validate(raw_manifest)
            raw_manifest = eseguibili_service.hydrate_manifest_connection_options(
                manifest,
                group_id=record.group_id,
            ).model_dump(mode="json")
        except ValueError:
            # Un set eliminato/non valido non deve rendere il dettaglio
            # dell'eseguibile illeggibile: la run produrrà un errore esplicito.
            raw_manifest = record.manifest
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
        "project_type": record.project_type,
        "active_version": record.active_version,
        "active_version_id": record.active_version_id,
        "manifest": raw_manifest,
        "has_manifest": has_manifest,
        "can_edit": None,
        "binding_warnings": eseguibili_service.binding_warnings(
            (record.manifest or {}).get("bindings"), canvas,
        ),
        "output_is_file_based": is_file_based,
        "inferred_output_extension": inferred_ext,
    }
