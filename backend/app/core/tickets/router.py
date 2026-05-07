"""
Router API — /api/tickets.

Endpoints:
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from app.core.auth.dependencies import get_current_user
from app.shared.contracts.auth import CurrentUser
from app.core.tickets import service as svc
from app.db import tickets_db_service as tdb
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/tickets", tags=["tickets"])


# MIME ALLOWLIST — solo immagini + log (da scelta deliberata

# Chiavi: estensione normalizzata (lowercase, no punto). Valori: MIME type
# atteso o None se non vincolato
_ALLOWED_EXTENSIONS = {
    # immagini
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    # log / testo
    "log": None,
    "txt": None,
}
# Cap configurabile via env. Default 10MB; alzare richiede valutare
# throughput rete (upload sincrono nel request handler
# dimensione del blob in DB se sotto soglia FILE_SOURCE_S3_THRESHOLD_BYTES
# Calcolato a import-time: per cambiare serve riavvio del processo
# BE-CONFIG-001D: default tipato in
# `Settings.DEFAULTS["TICKET_ATTACHMENT_MAX_BYTES"]=10 * 1024 * 1024`
# Calcolato a import-time: per cambiare serve riavvio del processo
# comportamento invariato pre-001D
from app.config import settings as _settings_cfg
_MAX_ATTACHMENT_SIZE_BYTES = int(_settings_cfg.get("TICKET_ATTACHMENT_MAX_BYTES"))


def _require_not_esecutore(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Guard: esecutore non ha accesso ai tickets (ruolo kiosk isolato)."""
    if current_user.role == "esecutore":
        raise HTTPException(403, "Accesso ai ticket non consentito")
    return current_user


# MODELS


class TicketCreateRequest(BaseModel):
    kind: str = Field(..., description="'tech_debt' o 'user_support'")
    title: str
    description: str = ""
    severity: str = "medium"
    category: str = "other"
    target_group_id: Optional[str] = Field(None, description="Solo superadmin + tech_debt")
    assignee: Optional[str] = Field(None, description="user_id da pre-assegnare (admin/superadmin)")


class TicketUpdateRequest(BaseModel):
    status: Optional[str] = None
    severity: Optional[str] = None
    category: Optional[str] = None
    # Sentinel: None = unassign, field missing = no change
    # Pydantic non distingue naturalmente "missing" vs "null", quindi
    # usiamo una str sentinel. Il client passa "__UNSET__" per non
    # toccare, oppure omette la chiave (→ dict.get ritorna il sentinel
    assignee: Any = "__UNSET__"
    title: Optional[str] = None
    description: Optional[str] = None


class CommentCreateRequest(BaseModel):
    body: str


# ENDPOINTS — META & COUNTS


@router.get("/meta")
def get_meta(_user: CurrentUser = Depends(_require_not_esecutore)) -> Dict[str, Any]:
    """Enum di supporto per popolare dropdown frontend.

    Sono esposti in un endpoint dedicato (invece che hardcoded nel FE)
    cosi' che estendere le categorie sul backend non richieda redeploy
    del frontend — al refresh la nuova categoria appare.
    """
    return {
        "kinds": list(svc.TICKET_KINDS),
        "severities": list(svc.TICKET_SEVERITIES),
        "statuses": list(svc.TICKET_STATUSES),
        "statuses_active": list(svc.TICKET_STATUSES_ACTIVE),
        "categories": svc._category_display(),
        "allowed_attachment_extensions": sorted(_ALLOWED_EXTENSIONS.keys()),
        "max_attachment_size_bytes": _MAX_ATTACHMENT_SIZE_BYTES,
    }


@router.get("/counts")
def get_counts(current_user: CurrentUser = Depends(_require_not_esecutore)) -> Dict[str, Any]:
    """Counts per badge sidebar e card dashboard.

    Scope:
      - superadmin → cross-group
      - admin → proprio group
      - user/viewer → i propri ticket (no badge sidebar per loro, ma
        serve al frontend per mostrare "hai N ticket aperti")
    """
    if current_user.role == "superadmin":
        group_filter = None
    elif current_user.role in ("admin",):
        group_filter = current_user.group_id
    else:
        # user/viewer: contiamo solo i loro
        total = tdb.count_tickets(
            status_in=list(svc.TICKET_STATUSES_ACTIVE),
        )
        # filtro per created_by richiede list + filter: usiamo list_tickets_for
        mine_active = svc.list_tickets_for(
            actor=current_user,
            status_in=list(svc.TICKET_STATUSES_ACTIVE),
        )
        return {
            "active_mine": len(mine_active),
            "by_severity": {},
        }

    active = tdb.count_tickets(
        group_id=group_filter,
        status_in=list(svc.TICKET_STATUSES_ACTIVE),
    )
    assigned_to_me = tdb.count_tickets(
        group_id=group_filter,
        assignee=current_user.user_id,
        status_in=list(svc.TICKET_STATUSES_ACTIVE),
    )
    by_sev = tdb.counts_by_severity(
        group_id=group_filter,
        status_in=list(svc.TICKET_STATUSES_ACTIVE),
    )
    return {
        "active_total": active,
        "active_assigned_to_me": assigned_to_me,
        "by_severity": by_sev,
    }


# ENDPOINTS — CRUD


@router.post("", status_code=201)
def create_ticket_endpoint(
    payload: TicketCreateRequest,
    current_user: CurrentUser = Depends(_require_not_esecutore),
) -> Dict[str, Any]:
    return svc.create_ticket(
        kind=payload.kind,
        title=payload.title,
        description=payload.description,
        severity=payload.severity,
        category=payload.category,
        actor=current_user,
        target_group_id=payload.target_group_id,
        assignee=payload.assignee,
    )


@router.get("")
def list_tickets_endpoint(
    kind: Optional[str] = None,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    assignee: Optional[str] = None,
    mine: bool = False,
    limit: int = 500,
    current_user: CurrentUser = Depends(_require_not_esecutore),
) -> Dict[str, Any]:
    status_list = [s.strip() for s in status.split(",")] if status else None
    sev_list = [s.strip() for s in severity.split(",")] if severity else None
    tickets = svc.list_tickets_for(
        actor=current_user,
        kind=kind,
        status_in=status_list,
        severity_in=sev_list,
        assignee=assignee,
        mine=mine,
        limit=limit,
    )
    return {"tickets": tickets}


@router.get("/{ticket_id}")
def get_ticket_endpoint(
    ticket_id: str,
    current_user: CurrentUser = Depends(_require_not_esecutore),
) -> Dict[str, Any]:
    return svc.get_ticket_with_details(ticket_id, current_user)


@router.patch("/{ticket_id}")
def update_ticket_endpoint(
    ticket_id: str,
    payload: TicketUpdateRequest,
    current_user: CurrentUser = Depends(_require_not_esecutore),
) -> Dict[str, Any]:
    return svc.update_ticket(
        ticket_id,
        actor=current_user,
        status=payload.status,
        severity=payload.severity,
        category=payload.category,
        assignee=payload.assignee,
        title=payload.title,
        description=payload.description,
    )


# COMMENTS


@router.post("/{ticket_id}/comments", status_code=201)
def add_comment_endpoint(
    ticket_id: str,
    payload: CommentCreateRequest,
    current_user: CurrentUser = Depends(_require_not_esecutore),
) -> Dict[str, Any]:
    return svc.add_comment(ticket_id, payload.body, current_user)


# ATTACHMENTS


@router.post("/{ticket_id}/attachments", status_code=201)
async def upload_attachment_endpoint(
    ticket_id: str,
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(_require_not_esecutore),
) -> Dict[str, Any]:
    """Upload allegato al ticket.

    Allowlist:
      - Estensione: png, jpg, jpeg, gif, webp, log, txt
      - Size max: 10MB
    """
    # Accesso al ticket (raise 404/403
    ticket = svc.check_access(tdb.get_ticket(ticket_id), current_user)

    filename = file.filename or "attachment"

    # Read + size check (PRIMA della validazione MIME per evitare di
    # leggere file giganti di cui poi rifiutiamo l'estensione
    content = await file.read()
    if len(content) > _MAX_ATTACHMENT_SIZE_BYTES:
        raise HTTPException(
            413,
            f"Allegato troppo grande ({len(content)} bytes). "
            f"Limite: {_MAX_ATTACHMENT_SIZE_BYTES} bytes",
        )
    if not content:
        raise HTTPException(400, "File allegato vuoto")

    # Validazione MIME content-based (filetype
    # rifiuta eseguibili rinominati, polyglot, content-extension mismatch
    # Pre-fix: solo allowlist estensione + warning su content_type del client
    # ignorabile da chiunque controlli il browser
    from app.utils.mime_validator import MimeMismatchError, validate_upload
    try:
        ext = validate_upload(
            content=content,
            filename=filename,
            allowed_extensions=set(_ALLOWED_EXTENSIONS.keys()),
        )
    except MimeMismatchError as e:
        logger.warning(
            f"Upload allegato ticket rifiutato (MIME): {filename!r} "
            f"by={current_user.username}: {e}",
            op="ticket_attach_reject",
        )
        raise HTTPException(400, str(e))

    # Salva via file_service (riusa storage dispatch DB/S3
    from app.services.file_service import FileSourceService
    fs = FileSourceService()
    file_source = fs.create_raw_from_upload(
        file_content=io.BytesIO(content),
        filename=filename,
        name=f"ticket-{ticket_id[:8]}-{filename}",
        description=f"Allegato ticket {ticket_id}",
        group_id=ticket["group_id"],
    )

    # Link al ticket
    attachment = tdb.link_attachment(
        ticket_id=ticket_id,
        file_source_id=file_source.id,
        uploaded_by=current_user.user_id,
    )
    logger.info(
        f"Allegato caricato su ticket {ticket_id}: {filename} "
        f"({len(content)} bytes) by={current_user.username}",
        op="ticket_attach",
    )
    return {
        **attachment,
        "file_name": file_source.name,
        "original_filename": file_source.original_filename,
        "format": file_source.format,
        "size_bytes": file_source.file_size,
    }


@router.delete("/{ticket_id}", status_code=204)
def delete_ticket_endpoint(
    ticket_id: str,
    hard: bool = False,
    current_user: CurrentUser = Depends(_require_not_esecutore),
) -> None:
    """Cancellazione ticket.

    Default: soft delete (set `deleted_at`, ticket sparisce dalle query
    ma audit trail preservato). Admin del proprio gruppo / superadmin.

    `?hard=true`: purge definitivo via FK cascade. Solo superadmin.
    Cleanup blob best-effort (errori loggati, blob orfani raccolti
    dalla lifecycle rule S3 / cleanup periodico DB).
    """
    result = svc.delete_ticket(ticket_id, current_user, hard=hard)

    if result.get("hard") and result.get("orphaned_file_source_ids"):
        from app.services.file_service import FileSourceService
        fs = FileSourceService()
        for fs_id in result["orphaned_file_source_ids"]:
            try:
                fs.delete_file_source(fs_id)
            except Exception as e:
                logger.warning(
                    f"Ticket {ticket_id} eliminato ma cleanup file_source "
                    f"{fs_id} fallito (orfano): {e}"
                )

    logger.info(
        f"Ticket {ticket_id} {'PURGED' if result.get('hard') else 'soft-deleted'} "
        f"by={current_user.username}",
        op="ticket_delete",
    )
    # F2-B3 (2026-04-24): DELETE risponde 204 No Content. La semantica
    # soft/hard resta visibile via `?hard=true` query param + logger op
    # "ticket_delete" (PURGED vs soft-deleted); non e' piu' echoed nel body


@router.get("/{ticket_id}/attachments/{attachment_id}/download")
def download_attachment_endpoint(
    ticket_id: str,
    attachment_id: str,
    current_user: CurrentUser = Depends(_require_not_esecutore),
) -> Response:
    """Scarica il blob dell'allegato.

    RBAC: stesso predicato di view del ticket (chi puo' vedere il ticket
    puo' scaricarne gli allegati). Risposta:
      - Content-Type: dal MIME dell'estensione (se in allowlist) o octet-stream
      - Content-Disposition: `inline` per immagini (viewabili nel browser),
        `attachment` per log/txt (force download).
    """
    ticket = svc.check_access(tdb.get_ticket(ticket_id), current_user)
    attachment = tdb.get_attachment(attachment_id)
    if not attachment or attachment["ticket_id"] != ticket_id:
        raise HTTPException(404, "Allegato non trovato")

    from app.services.file_service import FileSourceService
    fs = FileSourceService()
    blob = fs.get_blob_bytes(
        attachment["file_source_id"], expected_group_id=ticket["group_id"],
    )
    if blob is None:
        raise HTTPException(404, "Blob allegato non disponibile")

    # Recupera metadata del file_source per filename + format
    from app.db.config.file_sources import get_file_source as _get_file_source
    fs_row = _get_file_source(attachment["file_source_id"], include_data=False)
    if not fs_row:
        raise HTTPException(404, "File source non trovato")

    filename = fs_row.get("original_filename") or "attachment"
    fmt = (fs_row.get("format") or "").lower()

    # Content-Type dall'allowlist (resta autoritativa anche in download
    mime = _ALLOWED_EXTENSIONS.get(fmt) or "application/octet-stream"
    # Immagini inline, log/txt forzati come download
    disposition = "inline" if mime.startswith("image/") else "attachment"

    # filename sanitizzato per header (virgolette + no newline
    safe_name = filename.replace('"', "").replace("\r", "").replace("\n", "")
    headers = {
        "Content-Disposition": f'{disposition}; filename="{safe_name}"',
        "Content-Length": str(len(blob)),
    }
    return Response(content=blob, media_type=mime, headers=headers)


@router.delete("/{ticket_id}/attachments/{attachment_id}", status_code=204)
def delete_attachment_endpoint(
    ticket_id: str,
    attachment_id: str,
    current_user: CurrentUser = Depends(_require_not_esecutore),
) -> None:
    """Rimuove l'allegato dal ticket. Admin/superadmin sempre, uploader
    sui propri allegati."""
    ticket = svc.check_access(tdb.get_ticket(ticket_id), current_user)
    attachment = tdb.get_attachment(attachment_id)
    if not attachment or attachment["ticket_id"] != ticket_id:
        raise HTTPException(404, "Allegato non trovato")

    is_admin = current_user.role in ("admin", "superadmin")
    is_uploader = attachment["uploaded_by"] == current_user.user_id
    if not is_admin and not is_uploader:
        raise HTTPException(403, "Puoi rimuovere solo i tuoi allegati")

    tdb.delete_attachment(attachment_id)

    # Best-effort: elimina anche il file_source sottostante (pulizia storage
    try:
        from app.services.file_service import FileSourceService
        FileSourceService().delete_file_source(attachment["file_source_id"])
    except Exception as e:
        logger.warning(
            f"Allegato {attachment_id} rimosso ma cleanup file_source "
            f"{attachment['file_source_id']} fallito (orfano): {e}"
        )

    # F2-B3 (2026-04-24): DELETE risponde 204 No Content, body vuoto
