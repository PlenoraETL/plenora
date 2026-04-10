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
    name = Path(raw).name
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


def _assert_path_within(child: Path, parent: Path, label: str = "Percorso") -> Path:
    """Verifica che `child` (resolved) sia dentro `parent` (resolved).

    Difesa in profondità: anche se `_sanitize_upload_filename` ha già escluso
    path traversal nel nome, una eventuale presenza di symlink in `parent`
    potrebbe far uscire il `child` resolved fuori dalla sandbox.
    """
    try:
        child_resolved = child.resolve(strict=False)
        parent_resolved = parent.resolve(strict=False)
    except Exception:
        raise HTTPException(status_code=400, detail=f"{label} non valido")
    try:
        child_resolved.relative_to(parent_resolved)
    except ValueError:
        raise HTTPException(status_code=403, detail=f"{label} fuori dalla directory consentita")
    return child_resolved

from app.services.file_service import file_source_service
from app.config import settings
from app.utils.logger import get_logger
from app.core.auth.auth import (
    get_current_user,
    require_not_esecutore,
    require_user_or_above,
    require_file_owner,
    assert_group_access,
    assert_resource_visible,
    compute_can_edit,
    CurrentUser,
    group_filter,
)
from app.shared.sharing import visible_resource_ids
from .models import (
    FileSource,
    FileSourceList,
    FileSourceUpdate,
    FileSourceLayersResponse,
    FileSourcePreview,
)

logger = get_logger(__name__)

import app.db.db_service as _db_files


def _check_file_ownership(file_id: str, group_id: str, is_superadmin: bool = False):
    """Verifica che il file appartenga al gruppo. Solleva 404 o 403 altrimenti."""
    if is_superadmin:
        return
    row = _db_files.get_file_source(file_id, include_data=False)
    if not row:
        raise HTTPException(status_code=404, detail=f"File source '{file_id}' non trovato")
    if row.get("group_id") != group_id:
        raise HTTPException(status_code=403, detail="Accesso negato")

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


@router.post("/upload", response_model=FileSource)
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
        # Sanitizza filename con helper centralizzato (path traversal, NUL,
        # nomi riservati ".."/"."/"" )
        filename = _sanitize_upload_filename(file.filename)

        # SECURITY: enforce file size limit. settings.MAX_FILE_SIZE è definito
        # in config.py (default 100MB) ma non era mai enforced nell'endpoint.
        # Senza questo check, un utente può uploadare file di qualsiasi
        # dimensione → disk exhaustion DoS.
        file.file.seek(0, 2)  # seek to end
        file_size = file.file.tell()
        file.file.seek(0)    # rewind
        max_size = settings.get("MAX_FILE_SIZE", 100 * 1024 * 1024)
        if file_size > max_size:
            max_mb = max_size // (1024 * 1024)
            raise HTTPException(
                status_code=413,
                detail=f"File troppo grande ({file_size // (1024*1024)}MB). "
                       f"Limite massimo: {max_mb}MB."
            )

        valid_extensions = ['.csv', '.xlsx', '.xls', '.json', '.geojson', '.gpkg', '.shp', '.gdb', '.zip']

        if not any(filename.lower().endswith(ext) for ext in valid_extensions):
            raise HTTPException(
                status_code=400,
                detail=f"Formato file non supportato. Formati validi: {', '.join(valid_extensions)}"
            )
        
        # File binari/archivio: salva senza parsing (usati da executable)
        binary_extensions = ['.zip', '.gz', '.tar', '.7z', '.rar', '.dxf']
        if any(filename.lower().endswith(ext) for ext in binary_extensions):
            # Prima prova il parsing normale (es. ZIP con shapefile)
            try:
                file.file.seek(0)
                
                columns_list_bin = None
                if selected_columns:
                    import json
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
                # Parsing fallito per formato non supportato — salva come raw (es. ZIP con DXF)
                logger.warning(f"create_from_upload fallito per ZIP: {type(_bin_exc).__name__}: {_bin_exc}")
                pass
            
            import uuid
            from datetime import datetime
            from app.config import settings
            
            file.file.seek(0)
            file_id = str(uuid.uuid4())[:8]
            dest_dir = settings.get_file_source_dir(file_id)

            # filename è già stato sanitizzato all'inizio della funzione.
            # Difesa in profondità: verifichiamo che il percorso finale risolto
            # sia dentro dest_dir (protezione anti-symlink).
            safe_filename = _sanitize_upload_filename(filename)
            dest_path = dest_dir / safe_filename
            _assert_path_within(dest_path, dest_dir, label="Percorso file")

            content = file.file.read()
            dest_path.write_bytes(content)
            
            file_ext = safe_filename.rsplit('.', 1)[-1].lower()

            # Salva metadata
            import json
            metadata = {
                "id": file_id,
                "name": name,
                "description": description,
                "file_path": str(dest_path),
                "original_filename": safe_filename,
                "format": file_ext,
                "layer": None,
                "sheet": None,
                "columns": [],
                "row_count": 0,
                "file_size": len(content),
                "crs": None,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }
            
            metadata_path = dest_dir / "metadata.json"
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            
            return FileSource(**metadata)
        
        columns_list = None
        if selected_columns:
            import json
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


@router.post("/upload-multi-layer", response_model=List[FileSource])
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
        valid_extensions = ['.gpkg', '.gdb', '.zip', '.xlsx', '.xls']
        if not any(filename.lower().endswith(ext) for ext in valid_extensions):
            raise HTTPException(
                status_code=400,
                detail=f"upload-multi-layer supporta solo: {', '.join(valid_extensions)}"
            )

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
    Ogni record include `can_edit` calcolato per l'utente corrente."""
    is_super = current_user.role == "superadmin"

    # Carica TUTTI i file (senza filter) — il superadmin vede tutto.
    all_files = file_source_service.list_file_sources(group_id=None)

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


@router.delete("/{file_id}")
def delete_file(file_id: str, current_user: CurrentUser = Depends(require_file_owner)):
    """Elimina un file source e tutti i suoi file. Solo owner o admin."""
    success = file_source_service.delete_file_source(file_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"File source '{file_id}' non trovato")
    return {"status": "deleted", "file_id": file_id}


@router.get("/{file_id}/layers", response_model=FileSourceLayersResponse)
def get_file_layers(file_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Ottieni lista layer/sheet disponibili in un file. Read-only su risorsa visibile."""
    file_source = file_source_service.get_file_source(file_id)
    assert_resource_visible(file_source, "file_sources", current_user, "File")
    try:
        return file_source_service.get_layers_info(file_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"[CRASH] Errore listing layers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno del server")


@router.get("/{file_id}/preview", response_model=FileSourcePreview)
def preview_file(file_id: str, limit: int = Query(100, ge=1, le=1000), current_user: CurrentUser = Depends(require_not_esecutore)):
    """Ottieni preview dei dati del file. Read-only su risorsa visibile."""
    file_source = file_source_service.get_file_source(file_id)
    assert_resource_visible(file_source, "file_sources", current_user, "File")
    try:
        return file_source_service.get_preview(file_id, limit)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"[CRASH] Errore preview file: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno del server")
