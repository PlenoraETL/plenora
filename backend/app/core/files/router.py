"""
Router API per il modulo Files.

Gestione file sorgente per MANIPOLA e ARRICCHISCI.
"""

from pathlib import Path
from typing import Annotated, Optional, List

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query, Depends

from app.config import settings
from app.core.auth.dependencies import (
    require_not_esecutore,
    require_user_or_above,
)
from app.core.auth.owner_dependencies import require_file_owner
from app.core.auth.ownership import (
    assert_resource_visible,
    owns_or_admin,
)
from app.core.auth.visibility import visible_resource_ids
from app.core.files.service import FileSourceService, file_source_service
from app.observability.audit_action import audit_action
from app.shared import error_codes
from app.shared.contracts.auth import CurrentUser
from app.shared.error_envelope import raise_typed_http
from app.shared.resources.path_policy import assert_path_within as _core_assert
from app.core.security.upload_guards import enforce_upload_size_limit
from app.utils.logger import get_logger
from app.core.files import upload_boundary as _upload_boundary
from .models import (
    FileSource,
    FileSourceUpdate,
    FileSourceLayersResponse,
    FileSourceSummary,
    FileSourceSummaryList,
    FileSourcePreview,
)

def _sanitize_upload_filename(raw: Optional[str]) -> str:
    """Restituisce un nome file safe per il salvataggio su disco."""
    return _upload_boundary.sanitize_upload_filename(raw)

def _enforce_upload_size_limit(file: "UploadFile") -> int:
    """Wrapper locale al cap di upload: delega a `app.core.security.upload_guards`.

    L'implementazione canonica vive in `app/core/security/upload_guards.py`
    (T-04 + B1 riuso da `eseguibili/router.py`).

    Risolviamo `MAX_FILE_SIZE` dal `settings` di QUESTO modulo prima di
    invocare l'helper, cosi' i test che monkeypatchano `files_router.settings`
    continuano a controllare il cap (preserva il contratto di
    `tests2/security/unit/test_files_router_size_cap_contract.py`).
    """
    max_size = settings.get("MAX_FILE_SIZE", 100 * 1024 * 1024)
    return enforce_upload_size_limit(file, max_bytes=max_size)


def _assert_path_within(child: Path, parent: Path, label: str = "Percorso") -> Path:
    """Garantisce che `child` (resolved) sia dentro `parent`, traducendo
    eventuali violazioni in HTTPException con codice e messaggio
    HTTP-friendly.

    Contratto:
      - path malformato (UNC, resolve fallito) → HTTPException 400
      - path resolved fuori dalla sandbox → HTTPException 403
      - successo → ritorna il Path resolved

    `label` viene incluso nel messaggio response (es. "Percorso file
    non valido", "Upload fuori dalla directory consentita") per dare
    al client un errore comprensibile senza esporre dettagli del
    filesystem interno.

    Difesa in profondità: anche se il filename in upload e' stato gia'
    sanitizzato a monte, un symlink dentro `parent` potrebbe far uscire
    il `child` resolved fuori dalla sandbox — qui lo intercettiamo.
    """
    try:
        return _core_assert(child, [parent])
    except ValueError as e:
        msg = str(e)
        if "fuori dai trusted roots" in msg:
            raise HTTPException(
                status_code=403,
                detail=f"{label} fuori dalla directory consentita",
            )
        raise HTTPException(status_code=400, detail=f"{label} non valido")

logger = get_logger(__name__)


router = APIRouter(prefix="/files", tags=["File Sources"])


@router.post("/preview-temp")
def preview_file_temp(
    file: UploadFile = File(...),
    layer: Optional[str] = Form(None),
    sheet: Optional[str] = Form(None),
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Preview temporanea di un file per ottenere le colonne disponibili.

    - layer: layer specifico per GPKG/GDB
    - sheet: foglio specifico per XLSX
    Se nessuno e' specificato su file multi-layer/sheet, restituisce la lista disponibile.
    """
    # Size cap PRIMA del MIME read: senza questo guard, file.file.read
    # qui sotto carica N GB in RAM su qualsiasi POST autenticato (DoS
    # interno credenziato
    _enforce_upload_size_limit(file)

    _upload_boundary.validate_preview_content(
        file,
        logger=logger,
        username=current_user.username,
    )

    try:
        result = file_source_service.preview_file_temp(
            file_content=file.file,
            filename=file.filename,
            layer=layer,
            sheet=sheet
        )
        return result
    except (ValueError, Exception) as e:
        passthrough = _upload_boundary.passthrough_preview_response(file.filename)
        if passthrough is not None:
            return passthrough

        # Per altri formati, rilancia l'errore originale
        if isinstance(e, ValueError):
            raise HTTPException(status_code=400, detail=str(e))
        logger.error(f"[CRASH] Errore preview temp: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno durante la lettura del file")


@router.post("/upload", response_model=FileSource, status_code=201)
@audit_action(action="file.upload")
def upload_file(
    file: UploadFile = File(...),
    name: str = Form(...),
    description: str = Form(""),
    layer: Optional[str] = Form(None),
    sheet: Optional[str] = Form(None),
    selected_columns: Optional[str] = Form(None),
    srid: Optional[str] = Form(None),
    current_user: CurrentUser = Depends(require_user_or_above),
):
    """Upload un nuovo file e crea un File Source. Viewer non può creare."""
    try:
        # Sanitizza filename con helper centralizzato (path traversal, NUL
        # nomi riservati ".."/"."/""
        filename = _sanitize_upload_filename(file.filename)

        # Size cap pre-read (helper centralizzato, vedi `_enforce_upload_size_limit`
        _enforce_upload_size_limit(file)

        # MIME validation content-based: rifiuta eseguibili rinominati
        # polyglot, content-extension mismatch. Sostituisce la heuristic
        # pure-Python con `filetype` (zero dep sistema, ~200 MIME noti
        # coprono tutti i formati Plenora
        _upload_boundary.validate_upload_content(
            file,
            filename=filename,
            allowed_extensions=_upload_boundary.ALLOWED_UPLOAD_EXTENSIONS,
            logger=logger,
            username=current_user.username,
            context="Upload",
        )
        
        # File binari/archivio: salva senza parsing (usati da executable
        if _upload_boundary.is_binary_upload(filename):
            # Prima prova il parsing normale (es. ZIP con shapefile
            try:
                file.file.seek(0)
                
                columns_list_bin = _upload_boundary.parse_selected_columns(
                    selected_columns,
                    logger=logger,
                )
                srid_int_bin = _upload_boundary.parse_optional_int(
                    srid,
                    logger=logger,
                    label="SRID",
                )
                
                file_source = file_source_service.create_from_upload(
                    file_content=file.file,
                    filename=filename,
                    name=name,
                    description=description,
                    layer=layer,
                    sheet=sheet,
                    selected_columns=columns_list_bin,
                    srid=srid_int_bin,
                    group_id=current_user.group_id,
                )
                return file_source
            except ValueError:
                # ValueError esplicito (es. ZIP corrotto) → errore 400, non salvare come raw
                raise
            except Exception as _bin_exc:
                # Parsing fallito per formato non supportato (es. ZIP con
                # DXF interno, .dxf diretto). Fallback: salva blob raw nel
                # DB senza inferire colonne/layer. Il downstream (es
                # eseguibili workflow) legge il blob come bytes
                logger.warning(
                    f"create_from_upload fallito, fallback raw DB: "
                    f"{type(_bin_exc).__name__}: {_bin_exc}"
                )
                safe_filename = _sanitize_upload_filename(filename)
                file.file.seek(0)
                return file_source_service.create_raw_from_upload(
                    file_content=file.file,
                    filename=safe_filename,
                    name=name,
                    description=description,
                    group_id=current_user.group_id,
                )
        
        columns_list = _upload_boundary.parse_selected_columns(
            selected_columns,
            logger=logger,
        )
        srid_int = _upload_boundary.parse_optional_int(
            srid,
            logger=logger,
            label="SRID",
        )
        
        file_source = file_source_service.create_from_upload(
            file_content=file.file,
            filename=filename,
            name=name,
            description=description,
            layer=layer,
            sheet=sheet,
            selected_columns=columns_list,
            srid=srid_int,
            group_id=current_user.group_id,
        )
        
        return file_source
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[CRASH] Errore upload file: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno durante l'upload")


@router.post("/upload-multi-layer", response_model=List[FileSource], status_code=201)
@audit_action(action="file.upload_multi_layer")
def upload_multi_layer(
    file: UploadFile = File(...),
    name: str = Form(...),
    description: str = Form(""),
    layers_config: str = Form(...),
    srid: Optional[str] = Form(None),
    current_user: CurrentUser = Depends(require_user_or_above),
):
    """
    Upload un file multi-layer (GPKG, GDB) e crea N FileSource separati.

    layers_config: JSON array di oggetti con:
        - layer: nome layer
        - selected_columns: lista colonne (opzionale, null = tutte)
    
    Esempio:
        [
          {"layer": "reticoli", "selected_columns": ["id", "geometry"]},
          {"layer": "omi", "selected_columns": null}
        ]
    
    Returns: lista di FileSource creati, uno per layer.
    """
    try:
        filename = _sanitize_upload_filename(file.filename)

        # Size cap pre-read (helper centralizzato, vedi `_enforce_upload_size_limit`
        # Senza questo, il `file.file.read` per il MIME check sotto carica
        # in RAM N GB su qualsiasi POST autenticato user_or_above
        _enforce_upload_size_limit(file)

        _upload_boundary.validate_upload_content(
            file,
            filename=filename,
            allowed_extensions=_upload_boundary.ALLOWED_MULTI_LAYER_EXTENSIONS,
            logger=logger,
            username=current_user.username,
            context="Upload multi-layer",
        )

        layers_cfg = _upload_boundary.parse_layers_config(layers_config)
        srid_int = _upload_boundary.parse_optional_int(
            srid,
            logger=logger,
            label="SRID",
        )

        file_sources = file_source_service.create_multi_layer_from_upload(
            file_content=file.file,
            filename=filename,
            name=name,
            description=description,
            layers_config=layers_cfg,
            srid=srid_int,
            group_id=current_user.group_id,
        )

        return file_sources

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[CRASH] Errore upload multi-layer: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno durante l'upload")


@router.get("", response_model=FileSourceSummaryList)
def list_files(
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Lista file visibili: owned dal proprio gruppo + condivisi via allowed_groups.
    Ogni record include `can_edit` calcolato per l'utente corrente.

    I file_source temporanei creati dagli eseguibili (prefisso `__exec_temp_`)
    sono esclusi: esistono solo per ~20 secondi durante un'esecuzione e non
    devono mai apparire in UI — sono un dettaglio implementativo del runner
    dell'eseguibile, non contenuto utente.
    """
    is_super = current_user.role == "superadmin"

    # Carica TUTTI i file (senza filter) — il superadmin vede tutto
    all_files = None

    # Filtro file_source temporanei degli eseguibili (sempre, anche per superadmin
    if is_super:
        all_files = file_source_service.list_file_sources(
            group_id=None, limit=limit, offset=offset,
        )
    else:
        visible_ids = visible_resource_ids(
            'file_sources',
            current_user.group_id,
            is_superadmin=False,
        )
        all_files = file_source_service.list_file_sources(
            group_id=None,
            resource_ids=visible_ids,
            limit=limit,
            offset=offset,
        )

    # Inietta can_edit per ogni record
    files_iter = all_files.files if hasattr(all_files, 'files') else (all_files if isinstance(all_files, list) else [])
    for f in files_iter:
        # can_edit = la regola di PUT/DELETE (require_file_owner).
        f.can_edit = owns_or_admin(f, current_user)

    # UI-1 (C26): nella lista niente colonne, solo quante sono e i primi
    # nomi; il client chiede le pagine successive con limit/offset (C5).
    return FileSourceSummaryList(
        files=[FileSourceSummary.from_file_source(f) for f in files_iter]
    )


@router.get("/{file_id}", response_model=FileSource)
def get_file(file_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Dettaglio file. Read-only su risorsa visibile (owned + shared)."""
    file_source = file_source_service.get_file_source(file_id)
    assert_resource_visible(file_source, "file_sources", current_user, "File")
    file_source.can_edit = owns_or_admin(file_source, current_user)
    return file_source


@router.put("/{file_id}", response_model=FileSource)
@audit_action(action="file.update", target_param="file_id")
def update_file(
    file_id: str,
    data: FileSourceUpdate,
    current_user: CurrentUser = Depends(require_file_owner),
):
    """Aggiorna i metadati del FileSource e ricalcola lo schema del foglio/layer."""
    try:
        file_source = file_source_service.update_file_source(file_id, data)
        if file_source is None:
            raise HTTPException(status_code=404, detail="File non trovato")
        file_source.can_edit = True
        return file_source
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"[CRASH] Errore aggiornamento file: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Errore interno durante l'aggiornamento del file",
        ) from exc


@router.delete("/{file_id}", status_code=204)
@audit_action(action="file.delete", target_param="file_id")
def delete_file(file_id: str, current_user: CurrentUser = Depends(require_file_owner)):
    """Elimina un file source e tutti i suoi file. Solo owner o admin."""
    success = file_source_service.delete_file_source(file_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.FILE_SOURCE_NOT_FOUND,
                "message": f"File source '{file_id}' non trovato",
            },
        )
    # DELETE risponde 204 No Content, body vuoto.


def _svc_group(current_user: CurrentUser) -> str:
    """Calcola il group_id da passare al service per fail-closed.
    Superadmin usa il bypass sentinel del FileSourceService."""
    if current_user.role == "superadmin":
        return FileSourceService.SUPERADMIN_BYPASS
    return current_user.group_id


@router.get("/{file_id}/layers", response_model=FileSourceLayersResponse)
def get_file_layers(file_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Ottieni lista layer/sheet disponibili in un file. Read-only su risorsa visibile."""
    file_source = file_source_service.get_file_source(file_id)
    assert_resource_visible(file_source, "file_sources", current_user, "File")
    try:
        return file_source_service.get_layers_info(
            file_id, expected_group_id=_svc_group(current_user),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"[CRASH] Errore listing layers: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.get("/{file_id}/preview", response_model=FileSourcePreview)
def preview_file(file_id: str, limit: int = Query(100, ge=1, le=1000), current_user: CurrentUser = Depends(require_not_esecutore)):
    """Ottieni preview dei dati del file. Read-only su risorsa visibile."""
    file_source = file_source_service.get_file_source(file_id)
    assert_resource_visible(file_source, "file_sources", current_user, "File")
    try:
        return file_source_service.get_preview(
            file_id, limit, expected_group_id=_svc_group(current_user),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"[CRASH] Errore preview file: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")
