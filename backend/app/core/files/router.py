"""
Router API per il modulo Files.

Gestione file sorgente per MANIPOLA e ARRICCHISCI.
Spostato da app/api/files.py per rendere il modulo autocontenuto.

Endpoints:
- POST   /api/files/preview-temp  → preview temporanea senza salvare
- POST   /api/files/upload         → upload nuovo file
- GET    /api/files                → lista file sources
- GET    /api/files/{id}           → dettaglio file source
- DELETE /api/files/{id}           → elimina file
- GET    /api/files/{id}/layers    → lista layers/sheets
- GET    /api/files/{id}/preview   → anteprima dati
- GET    /api/files/{id}/count     → conteggio righe
"""

from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query, Depends
from typing import Optional, List


# Nomi file riservati che NON devono mai diventare il filename finale
# (anche se sopravvivono a Path(name).name).
_FORBIDDEN_BASENAMES = {"", ".", ".."}


def _sanitize_upload_filename(raw: Optional[str]) -> str:
    """Restituisce un nome file safe per il salvataggio su disco.

    - Estrae il basename (rimuove qualsiasi componente di path)
    - Rifiuta nomi vuoti / "." / ".."
    - Strippa NUL bytes (filesystem injection)
    - Strippa whitespace / separatori di path residui
    Solleva HTTPException 400 se il filename non è valido.
    """
    if not raw:
        raise HTTPException(status_code=400, detail="Nome file non valido")
    # Normalizza backslash -> forward slash PRIMA di Path(raw).name. Su
    # POSIX (CI Linux runner, container backend) pathlib.PosixPath non
    # riconosce '\' come separatore: senza questa pre-normalizzazione,
    # input Windows-style come "..\\nested\\report.csv" sopravvivrebbero
    # intatti come basename, bypassando il filtro path traversal. Cross-
    # platform safe: input gia' POSIX ('/') resta invariato dopo replace.
    name = Path(raw.replace("\\", "/")).name
    # Anche dopo Path().name, certi input strani sopravvivono (es. ".." → "..")
    if name in _FORBIDDEN_BASENAMES:
        raise HTTPException(status_code=400, detail="Nome file non valido")
    # NUL byte: alcuni filesystem lo trattano come terminator (path injection)
    if "\x00" in name:
        raise HTTPException(status_code=400, detail="Nome file contiene caratteri non validi")
    # Strip aggressivo di whitespace + separatori di path
    name = name.strip().lstrip("/\\")
    if not name or name in _FORBIDDEN_BASENAMES:
        raise HTTPException(status_code=400, detail="Nome file non valido")
    return name


def _enforce_upload_size_limit(file: "UploadFile") -> int:
    """Wrapper locale al cap di upload: delega a `app.shared.upload_guards`.

    L'implementazione canonica vive in `app/shared/upload_guards.py`
    (T-04 + B1 Phase 4: riuso da `eseguibili/router.py`).

    Risolviamo `MAX_FILE_SIZE` dal `settings` di QUESTO modulo prima di
    invocare l'helper, cosi' i test che monkeypatchano `files_router.settings`
    continuano a controllare il cap (preserva il contratto di
    `tests2/security/unit/test_files_router_size_cap_contract.py`).
    """
    from app.shared.upload_guards import enforce_upload_size_limit
    max_size = settings.get("MAX_FILE_SIZE", 100 * 1024 * 1024)
    return enforce_upload_size_limit(file, max_bytes=max_size)


def _assert_path_within(child: Path, parent: Path, label: str = "Percorso") -> Path:
    """Garantisce che `child` (resolved) sia dentro `parent`, traducendo
    eventuali violazioni in HTTPException con codice e messaggio
    HTTP-friendly.

    Contratto:
      - path malformato (UNC, resolve fallito) → HTTPException 400
      - path resolved fuori dalla sandbox       → HTTPException 403
      - successo                                 → ritorna il Path resolved

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

from app.services.file_service import file_source_service
from app.config import settings
from app.utils.logger import get_logger
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http
from app.shared.audit_action import audit_action
from app.core.auth.dependencies import (
    get_current_user,
    require_not_esecutore,
    require_user_or_above,
)
from app.core.auth.owner_dependencies import require_file_owner
# BE-GO-READY-AUTH-ENGINE-013 Phase-7 contract fan-out: pure
# contract symbols (`CurrentUser`, `group_filter`) consumed from
# the shared contract module. DB-bound ownership helpers stay on
# `app.core.auth.ownership` (their canonical home - they require
# `app.db.db_service` for `compute_can_edit` / `assert_can_modify`).
# FastAPI Depends + `require_*` stay on the auth facade. Facade
# KEEP_COMPAT re-exports preserved.
from app.shared.contracts.auth import CurrentUser, group_filter
from app.core.auth.ownership import (
    assert_group_access,
    assert_resource_visible,
    compute_can_edit,
)
from app.shared.sharing import visible_resource_ids
from app.shared.path_policy import assert_path_within as _core_assert
from .models import (
    FileSource,
    FileSourceList,
    FileSourceUpdate,
    FileSourceLayersResponse,
    FileSourcePreview,
)

logger = get_logger(__name__)

from app.db.config.file_sources import get_file_source as _get_file_source


def _check_file_ownership(file_id: str, group_id: str, is_superadmin: bool = False):
    """Verifica che il file appartenga al gruppo. Solleva 404 o 403 altrimenti."""
    if is_superadmin:
        return
    row = _get_file_source(file_id, include_data=False)
    if not row:
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.FILE_SOURCE_NOT_FOUND,
                "message": f"File source '{file_id}' non trovato",
            },
        )
    if row.get("group_id") != group_id:
        raise_typed_http(
            code=error_codes.ACCESS_DENIED,
            status_code=403,
            message="Accesso negato",
        )

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
    # Size cap PRIMA del MIME read: senza questo guard, file.file.read()
    # qui sotto carica N GB in RAM su qualsiasi POST autenticato (DoS
    # interno credenziato).
    _enforce_upload_size_limit(file)

    # Block-only MIME validation: blocca eseguibili anche in preview
    # (no persist ma evita di passare un binario al parser, riducendo
    # superficie RCE da parser bug). Allowlist stretta romperebbe i
    # passthrough_extensions documentati sotto.
    from app.utils.mime_validator import (
        MimeMismatchError, validate_no_executable,
    )
    file.file.seek(0)
    _content_for_mime = file.file.read()
    file.file.seek(0)
    try:
        validate_no_executable(
            content=_content_for_mime, filename=file.filename or "upload",
        )
    except MimeMismatchError as _mime_err:
        logger.warning(
            f"Preview rifiutata (MIME): filename={file.filename!r} "
            f"user={current_user.username}: {_mime_err}"
        )
        raise HTTPException(status_code=400, detail=str(_mime_err))

    try:
        result = file_source_service.preview_file_temp(
            file_content=file.file,
            filename=file.filename,
            layer=layer,
            sheet=sheet
        )
        return result
    except (ValueError, Exception) as e:
        # File non parsabile (es. ZIP con DXF, archivi generici)
        # Ritorna preview vuota — il file può ancora essere usato da executable
        file_ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        passthrough_extensions = ['zip', 'gz', 'tar', '7z', 'rar', 'dxf']
        
        if file_ext in passthrough_extensions:
            return {
                "columns": [],
                "rows": [],
                "total_count": 0,
                "preview_count": 0,
                "format": file_ext,
                "message": f"File .{file_ext} non parsabile come tabulare — utilizzabile con Eseguibile Locale"
            }
        
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
    import json, uuid
    from datetime import datetime
    try:
        # Sanitizza filename con helper centralizzato (path traversal, NUL,
        # nomi riservati ".."/"."/"" )
        filename = _sanitize_upload_filename(file.filename)

        # Size cap pre-read (helper centralizzato, vedi `_enforce_upload_size_limit`).
        _enforce_upload_size_limit(file)

        # MIME validation content-based: rifiuta eseguibili rinominati,
        # polyglot, content-extension mismatch. Sostituisce la heuristic
        # pure-Python con `filetype` (zero dep sistema, ~200 MIME noti
        # coprono tutti i formati Plenora).
        from app.utils.mime_validator import MimeMismatchError, validate_upload

        _ALLOWED_UPLOAD_EXTENSIONS = {
            "csv", "xlsx", "xls", "json", "geojson",
            "gpkg", "shp", "gdb", "zip", "dxf",
        }
        file.file.seek(0)
        content_for_mime = file.file.read()
        file.file.seek(0)
        try:
            validate_upload(
                content=content_for_mime,
                filename=filename,
                allowed_extensions=_ALLOWED_UPLOAD_EXTENSIONS,
            )
        except MimeMismatchError as e:
            logger.warning(
                f"Upload rifiutato (MIME): filename={filename!r} "
                f"user={current_user.username}: {e}"
            )
            raise HTTPException(status_code=400, detail=str(e))
        
        # File binari/archivio: salva senza parsing (usati da executable)
        binary_extensions = ['.zip', '.gz', '.tar', '.7z', '.rar', '.dxf']
        if any(filename.lower().endswith(ext) for ext in binary_extensions):
            # Prima prova il parsing normale (es. ZIP con shapefile)
            try:
                file.file.seek(0)
                
                columns_list_bin = None
                if selected_columns:
                    try:
                        columns_list_bin = json.loads(selected_columns)
                    except json.JSONDecodeError:
                        pass
                
                srid_int_bin = None
                if srid:
                    try:
                        srid_int_bin = int(srid)
                    except ValueError:
                        pass
                
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
                # DB senza inferire colonne/layer. Il downstream (es.
                # eseguibili workflow) legge il blob come bytes.
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
        
        columns_list = None
        if selected_columns:
            try:
                columns_list = json.loads(selected_columns)
            except json.JSONDecodeError:
                logger.warning(f"Errore parsing selected_columns: {selected_columns}")
        
        srid_int = None
        if srid:
            try:
                srid_int = int(srid)
            except ValueError:
                logger.warning(f"SRID non valido: {srid}")
        
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
    import json as _json
    try:
        filename = Path(file.filename).name if file.filename else ""
        if not filename:
            raise HTTPException(status_code=400, detail="Nome file non valido")

        # Size cap pre-read (helper centralizzato, vedi `_enforce_upload_size_limit`).
        # Senza questo, il `file.file.read()` per il MIME check sotto carica
        # in RAM N GB su qualsiasi POST autenticato user_or_above.
        _enforce_upload_size_limit(file)

        # MIME validation content-based (stessa difesa di /upload).
        from app.utils.mime_validator import MimeMismatchError, validate_upload
        _ALLOWED_MULTI_LAYER_EXTENSIONS = {"gpkg", "gdb", "zip", "xlsx", "xls"}
        file.file.seek(0)
        content_for_mime = file.file.read()
        file.file.seek(0)
        try:
            validate_upload(
                content=content_for_mime,
                filename=filename,
                allowed_extensions=_ALLOWED_MULTI_LAYER_EXTENSIONS,
            )
        except MimeMismatchError as e:
            logger.warning(
                f"Upload multi-layer rifiutato (MIME): filename={filename!r} "
                f"user={current_user.username}: {e}"
            )
            raise HTTPException(status_code=400, detail=str(e))

        try:
            layers_cfg = _json.loads(layers_config)
        except _json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="layers_config non e' JSON valido")

        if not isinstance(layers_cfg, list) or len(layers_cfg) == 0:
            raise HTTPException(status_code=400, detail="layers_config deve essere un array non vuoto")

        srid_int = None
        if srid:
            try:
                srid_int = int(srid)
            except ValueError:
                pass

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


@router.get("", response_model=FileSourceList)
def list_files(current_user: CurrentUser = Depends(require_not_esecutore)):
    """Lista file visibili: owned dal proprio gruppo + condivisi via allowed_groups.
    Ogni record include `can_edit` calcolato per l'utente corrente.

    I file_source temporanei creati dagli eseguibili (prefisso `__exec_temp_`)
    sono esclusi: esistono solo per ~20 secondi durante un'esecuzione e non
    devono mai apparire in UI — sono un dettaglio implementativo del runner
    dell'eseguibile, non contenuto utente.
    """
    is_super = current_user.role == "superadmin"

    # Carica TUTTI i file (senza filter) — il superadmin vede tutto.
    all_files = file_source_service.list_file_sources(group_id=None)

    # Filtro file_source temporanei degli eseguibili (sempre, anche per superadmin).
    def _is_temp(f):
        name = getattr(f, "name", "") or ""
        return name.startswith("__exec_temp_")

    if hasattr(all_files, 'files'):
        all_files.files = [f for f in all_files.files if not _is_temp(f)]
    elif isinstance(all_files, list):
        all_files = [f for f in all_files if not _is_temp(f)]

    if not is_super:
        visible_ids = visible_resource_ids(
            'file_sources',
            current_user.group_id,
            is_superadmin=False,
        )
        if hasattr(all_files, 'files'):
            all_files.files = [f for f in all_files.files if f.id in visible_ids]
        elif isinstance(all_files, list):
            all_files = [f for f in all_files if f.id in visible_ids]

    # Inietta can_edit per ogni record
    files_iter = all_files.files if hasattr(all_files, 'files') else (all_files if isinstance(all_files, list) else [])
    for f in files_iter:
        f.can_edit = compute_can_edit(f, current_user)

    return all_files


@router.get("/{file_id}", response_model=FileSource)
def get_file(file_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Dettaglio file. Read-only su risorsa visibile (owned + shared)."""
    file_source = file_source_service.get_file_source(file_id)
    assert_resource_visible(file_source, "file_sources", current_user, "File")
    file_source.can_edit = compute_can_edit(file_source, current_user)
    return file_source


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
    # F2-B3 (2026-04-24): DELETE risponde 204 No Content, body vuoto.


def _svc_group(current_user: CurrentUser) -> str:
    """N1: calcola il group_id da passare al service per fail-closed.
    Superadmin usa il bypass sentinel del FileSourceService."""
    from app.services.file_service import FileSourceService
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
