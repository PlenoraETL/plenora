"""
Router API — /api/tickets.

Endpoints:
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings as _settings_cfg
from app.core.auth.dependencies import get_current_user
from app.core.files import db as files_db
from app.core.files import service as file_service_module
from app.shared.contracts.auth import CurrentUser
from app.core.tickets import attachment_boundary as _attachment_boundary
from app.core.tickets import db as tdb
from app.core.tickets import service as svc
from app.shared.contracts.payloads import JsonObject
from app.utils.logger import get_logger
from app.core.io.bounded_streams import read_prefix
from app.core.security.upload_guards import enforce_upload_size_limit

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
# Gli allegati usano lo stesso object storage dei file source.
# Calcolato a import-time: per cambiare serve riavvio del processo
# Calcolato a import-time: per cambiare serve riavvio del processo.
_MAX_ATTACHMENT_SIZE_BYTES = int(_settings_cfg.get("TICKET_ATTACHMENT_MAX_BYTES"))


def _row_dict(row: object) -> JsonObject:
    return jsonable_encoder(asdict(row))


def _ticket_details_dict(details: svc.TicketDetails) -> JsonObject:
    return {
        "ticket": _row_dict(details.ticket),
        "comments": [_row_dict(comment) for comment in details.comments],
        "attachments": [_row_dict(attachment) for attachment in details.attachments],
    }


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
def get_meta(_user: CurrentUser = Depends(_require_not_esecutore)) -> JsonObject:
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
def get_counts(current_user: CurrentUser = Depends(_require_not_esecutore)) -> JsonObject:
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
) -> JsonObject:
    ticket = svc.create_ticket(
        kind=payload.kind,
        title=payload.title,
        description=payload.description,
        severity=payload.severity,
        category=payload.category,
        actor=current_user,
        target_group_id=payload.target_group_id,
        assignee=payload.assignee,
    )
    return _row_dict(ticket)


@router.get("")
def list_tickets_endpoint(
    kind: Optional[str] = None,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    assignee: Optional[str] = None,
    mine: bool = False,
    limit: int = 500,
    current_user: CurrentUser = Depends(_require_not_esecutore),
) -> JsonObject:
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
    return {"tickets": [_row_dict(ticket) for ticket in tickets]}


@router.get("/{ticket_id}")
def get_ticket_endpoint(
    ticket_id: str,
    current_user: CurrentUser = Depends(_require_not_esecutore),
) -> JsonObject:
    return _ticket_details_dict(svc.get_ticket_with_details(ticket_id, current_user))


@router.patch("/{ticket_id}")
def update_ticket_endpoint(
    ticket_id: str,
    payload: TicketUpdateRequest,
    current_user: CurrentUser = Depends(_require_not_esecutore),
) -> JsonObject:
    ticket = svc.update_ticket(
        ticket_id,
        actor=current_user,
        status=payload.status,
        severity=payload.severity,
        category=payload.category,
        assignee=payload.assignee,
        title=payload.title,
        description=payload.description,
    )
    return _row_dict(ticket)


# COMMENTS


@router.post("/{ticket_id}/comments", status_code=201)
def add_comment_endpoint(
    ticket_id: str,
    payload: CommentCreateRequest,
    current_user: CurrentUser = Depends(_require_not_esecutore),
) -> JsonObject:
    return _row_dict(svc.add_comment(ticket_id, payload.body, current_user))


# ATTACHMENTS


@router.post("/{ticket_id}/attachments", status_code=201)
async def upload_attachment_endpoint(
    ticket_id: str,
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(_require_not_esecutore),
) -> JsonObject:
    """Upload allegato al ticket.

    Allowlist:
      - Estensione: png, jpg, jpeg, gif, webp, log, txt
      - Size max: 10MB
    """
    # Accesso al ticket (raise 404/403
    ticket = svc.check_access(tdb.get_ticket(ticket_id), current_user)

    filename = file.filename or "attachment"

    size_bytes = enforce_upload_size_limit(
        file,
        max_bytes=_MAX_ATTACHMENT_SIZE_BYTES,
    )
    content_header = read_prefix(file.file)
    _attachment_boundary.validate_attachment_upload(
        content=content_header,
        filename=filename,
        max_bytes=_MAX_ATTACHMENT_SIZE_BYTES,
        size_bytes=size_bytes,
        allowed_extensions=set(_ALLOWED_EXTENSIONS.keys()),
        logger=logger,
        username=current_user.username,
    )

    # Salva via file_service (riusa storage dispatch DB/S3
    fs = file_service_module.FileSourceService()
    file_source = fs.create_raw_from_upload(
        file_content=file.file,
        filename=filename,
        name=f"ticket-{ticket_id[:8]}-{filename}",
        description=f"Allegato ticket {ticket_id}",
        group_id=ticket.group_id,
    )

    # Link al ticket
    attachment = tdb.link_attachment(
        ticket_id=ticket_id,
        file_source_id=file_source.id,
        uploaded_by=current_user.user_id,
    )
    logger.info(
        f"Allegato caricato su ticket {ticket_id}: {filename} "
        f"({size_bytes} bytes) by={current_user.username}",
        op="ticket_attach",
    )
    return {
        **_row_dict(attachment),
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

    if result.hard and result.orphaned_file_source_ids:
        fs = file_service_module.FileSourceService()
        for fs_id in result.orphaned_file_source_ids:
            try:
                fs.delete_file_source(fs_id)
            except Exception as e:
                logger.warning(
                    f"Ticket {ticket_id} eliminato ma cleanup file_source "
                    f"{fs_id} fallito (orfano): {e}"
                )

    logger.info(
        f"Ticket {ticket_id} {'PURGED' if result.hard else 'soft-deleted'} "
        f"by={current_user.username}",
        op="ticket_delete",
    )
    # DELETE risponde 204 No Content; la semantica soft/hard resta visibile
    # via `?hard=true` e nel log op "ticket_delete".


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
    if not attachment or attachment.ticket_id != ticket_id:
        raise HTTPException(404, "Allegato non trovato")

    fs = file_service_module.FileSourceService()
    resource = fs.get_blob_stream(
        attachment.file_source_id, expected_group_id=ticket.group_id,
    )
    if resource is None or resource.stream is None:
        raise HTTPException(404, "Blob allegato non disponibile")

    # Recupera metadata del file_source per filename + format
    fs_row = files_db.get_file_source(attachment.file_source_id, include_data=False)
    if not fs_row:
        raise HTTPException(404, "File source non trovato")

    filename = fs_row.get("original_filename") or "attachment"
    fmt = (fs_row.get("format") or "").lower()

    mime, headers = _attachment_boundary.download_headers(
        filename=filename,
        fmt=fmt,
        blob_size=int(fs_row.get("size_bytes") or resource.size_bytes or 0),
        allowed_extensions=_ALLOWED_EXTENSIONS,
    )
    return StreamingResponse(
        resource.stream,
        media_type=mime,
        headers=headers,
    )


@router.delete("/{ticket_id}/attachments/{attachment_id}", status_code=204)
def delete_attachment_endpoint(
    ticket_id: str,
    attachment_id: str,
    current_user: CurrentUser = Depends(_require_not_esecutore),
) -> None:
    """Rimuove l'allegato dal ticket. Admin/superadmin sempre, uploader
    sui propri allegati."""
    svc.check_access(tdb.get_ticket(ticket_id), current_user)
    attachment = tdb.get_attachment(attachment_id)
    if not attachment or attachment.ticket_id != ticket_id:
        raise HTTPException(404, "Allegato non trovato")

    is_admin = current_user.role in ("admin", "superadmin")
    is_uploader = attachment.uploaded_by == current_user.user_id
    if not is_admin and not is_uploader:
        raise HTTPException(403, "Puoi rimuovere solo i tuoi allegati")

    tdb.delete_attachment(attachment_id)

    # Best-effort: elimina anche il file_source sottostante (pulizia storage
    try:
        file_service_module.FileSourceService().delete_file_source(
            attachment.file_source_id
        )
    except Exception as e:
        logger.warning(
            f"Allegato {attachment_id} rimosso ma cleanup file_source "
            f"{attachment.file_source_id} fallito (orfano): {e}"
        )

    # DELETE risponde 204 No Content, body vuoto.
