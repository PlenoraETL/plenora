"""
Router FastAPI per gli eseguibili.

Endpoint:
- GET    /api/eseguibili                 — lista del gruppo
- POST   /api/eseguibili                 — crea da progetto arricchisci (solo admin)
- GET    /api/eseguibili/{id}            — dettagli con expected_columns
- PUT    /api/eseguibili/{id}            — aggiorna metadata (solo admin)
- DELETE /api/eseguibili/{id}            — elimina (solo admin)
- POST   /api/eseguibili/{id}/analyze-upload  — upload file + analisi colonne
- POST   /api/eseguibili/{id}/execute         — esegue con mapping + lancia task
"""

import os
import tempfile
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from typing import List, Optional

from app.core.auth.auth import get_current_user, CurrentUser, group_filter
from app.config import settings
from app.utils.logger import get_logger

from . import db as eseguibili_db
from . import service as eseguibili_service
from .models import (
    EseguibileCreate, EseguibileUpdate, EseguibileSummary, EseguibileDetails,
    ExecuteRequest, ExpectedColumn, AnalyzeUploadResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/eseguibili", tags=["eseguibili"])


def _require_admin(user: CurrentUser) -> None:
    """Solo admin/superadmin possono creare/modificare/eliminare eseguibili."""
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(
            status_code=403,
            detail="Solo gli admin del gruppo possono pubblicare o modificare eseguibili"
        )


def _check_group_access(eseguibile: dict, user: CurrentUser) -> None:
    """Verifica che l'utente possa accedere all'eseguibile (stesso gruppo o superadmin)."""
    if user.role == "superadmin":
        return
    if eseguibile.get("group_id") != user.group_id:
        raise HTTPException(status_code=403, detail="Accesso negato: eseguibile di altro gruppo")


# ──────────────────────────────────────────────────────────────
# LIST
# ──────────────────────────────────────────────────────────────

@router.get("", response_model=List[EseguibileSummary])
def list_eseguibili(current_user: CurrentUser = Depends(get_current_user)):
    """Lista gli eseguibili visibili all'utente (del suo gruppo)."""
    gid = group_filter(current_user)
    rows = eseguibili_db.list_eseguibili(group_id=gid)
    return rows


# ──────────────────────────────────────────────────────────────
# CREATE (solo admin)
# ──────────────────────────────────────────────────────────────

@router.post("", response_model=EseguibileDetails)
def create_eseguibile(
    payload: EseguibileCreate,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Crea un eseguibile clonando il canvas di un progetto arricchisci esistente.

    Solo gli admin del gruppo possono pubblicare. Il progetto sorgente deve appartenere
    allo stesso gruppo dell'admin (o qualsiasi gruppo se superadmin).
    """
    _require_admin(current_user)
    try:
        record = eseguibili_service.create_from_project(
            source_project_id=payload.source_project_id,
            name=payload.name,
            group_id=current_user.group_id,
            created_by=current_user.user_id,
            description=payload.description or "",
            default_output_format=payload.default_output_format,
            default_output_name=payload.default_output_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[CRASH] Errore creazione eseguibile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

    return _to_details(record)


# ──────────────────────────────────────────────────────────────
# GET ONE
# ──────────────────────────────────────────────────────────────

@router.get("/{eseguibile_id}", response_model=EseguibileDetails)
def get_eseguibile(
    eseguibile_id: str,
    current_user: CurrentUser = Depends(get_current_user)
):
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    if not record:
        raise HTTPException(status_code=404, detail="Eseguibile non trovato")
    _check_group_access(record, current_user)
    return _to_details(record)


# ──────────────────────────────────────────────────────────────
# UPDATE (solo admin)
# ──────────────────────────────────────────────────────────────

@router.put("/{eseguibile_id}", response_model=EseguibileDetails)
def update_eseguibile(
    eseguibile_id: str,
    payload: EseguibileUpdate,
    current_user: CurrentUser = Depends(get_current_user)
):
    _require_admin(current_user)
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    if not record:
        raise HTTPException(status_code=404, detail="Eseguibile non trovato")
    _check_group_access(record, current_user)

    updated = eseguibili_db.update_eseguibile(
        eseguibile_id,
        name=payload.name,
        description=payload.description,
        default_output_format=payload.default_output_format,
        default_output_name=payload.default_output_name,
    )
    return _to_details(updated)


# ──────────────────────────────────────────────────────────────
# DELETE (solo admin)
# ──────────────────────────────────────────────────────────────

@router.delete("/{eseguibile_id}")
def delete_eseguibile(
    eseguibile_id: str,
    current_user: CurrentUser = Depends(get_current_user)
):
    _require_admin(current_user)
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    if not record:
        raise HTTPException(status_code=404, detail="Eseguibile non trovato")
    _check_group_access(record, current_user)

    ok = eseguibili_db.delete_eseguibile(eseguibile_id)
    return {"success": ok}


# ──────────────────────────────────────────────────────────────
# ANALYZE UPLOAD
# ──────────────────────────────────────────────────────────────

# Cache in-memory dei file caricati (Phase 1: processo singolo)
# In futuro: salvarli su disco con TTL, o usare Redis
_UPLOAD_CACHE: dict = {}


@router.post("/{eseguibile_id}/analyze-upload", response_model=AnalyzeUploadResponse)
async def analyze_upload(
    eseguibile_id: str,
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Upload file utente, parse e restituisce colonne rilevate + auto-mapping.

    Il file viene salvato in una cartella temp. Il file_id ritornato viene poi usato
    nel successivo POST /execute. I file temp vengono eliminati dopo l'esecuzione.
    """
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    if not record:
        raise HTTPException(status_code=404, detail="Eseguibile non trovato")
    _check_group_access(record, current_user)

    # Salva in temp dir
    tmp_dir = settings.DATA_DIR / "temp" / "eseguibili_uploads"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(file.filename).suffix or ".bin"
    tmp_file = tempfile.NamedTemporaryFile(
        delete=False, suffix=suffix, dir=str(tmp_dir)
    )
    try:
        content = await file.read()
        tmp_file.write(content)
        tmp_file.close()
        tmp_path = tmp_file.name

        try:
            result = eseguibili_service.analyze_file_upload(
                eseguibile=record,
                file_path=tmp_path,
                filename=file.filename,
            )
        except ValueError as e:
            # Cleanup file temp se analisi fallisce
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise HTTPException(status_code=400, detail=str(e))

        # Cache del path temp per l'execute successivo
        _UPLOAD_CACHE[result["file_id"]] = {
            "path": result["file_path"],
            "filename": result["filename"],
            "eseguibile_id": eseguibile_id,
            "user_id": current_user.user_id,
        }

        # Rimuovi il file_path dal response (non serve al frontend)
        result.pop("file_path", None)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore analyze upload: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────
# EXECUTE
# ──────────────────────────────────────────────────────────────

@router.post("/{eseguibile_id}/execute")
def execute_eseguibile(
    eseguibile_id: str,
    payload: ExecuteRequest,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Esegue un eseguibile: rinomina colonne secondo il mapping, clona il canvas,
    sostituisce la sorgente con il file caricato e lancia il task di enrichment.
    """
    record = eseguibili_db.get_eseguibile(eseguibile_id)
    if not record:
        raise HTTPException(status_code=404, detail="Eseguibile non trovato")
    _check_group_access(record, current_user)

    # Recupera il file dal cache upload
    upload = _UPLOAD_CACHE.get(payload.file_id)
    if not upload:
        raise HTTPException(
            status_code=400,
            detail="File upload non trovato o scaduto. Ricarica il file."
        )
    if upload["eseguibile_id"] != eseguibile_id:
        raise HTTPException(status_code=400, detail="File upload non corrisponde all'eseguibile")

    try:
        result = eseguibili_service.execute(
            eseguibile=record,
            file_path=upload["path"],
            column_mapping=[m.model_dump() for m in payload.column_mapping],
            output_format=payload.output_format,
            output_name=payload.output_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[CRASH] Errore execute eseguibile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Cleanup: elimina file temp e voce cache
        try:
            if os.path.exists(upload["path"]):
                os.unlink(upload["path"])
        except Exception:
            pass
        _UPLOAD_CACHE.pop(payload.file_id, None)

    return result


# ──────────────────────────────────────────────────────────────
# HELPER
# ──────────────────────────────────────────────────────────────

def _to_details(record: dict) -> dict:
    """Converte un record DB in EseguibileDetails response."""
    return {
        "id": record["id"],
        "name": record["name"],
        "description": record.get("description") or "",
        "expected_columns": record.get("expected_columns") or [],
        "default_output_format": record.get("default_output_format", "xlsx"),
        "default_output_name": record.get("default_output_name"),
        "source_canvas_id": record.get("source_canvas_id"),
        "group_id": record["group_id"],
        "created_by": record["created_by"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
    }
