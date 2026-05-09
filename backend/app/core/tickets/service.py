"""
Ticket service — orchestrazione, RBAC, validazione, hook notifiche.

Policy RBAC:
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Tuple

from fastapi import HTTPException

from app.shared.contracts.auth import CurrentUser
from app.core.tickets import db as tdb
from app.shared import error_codes
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class TicketDetails:
    ticket: tdb.TicketRow
    comments: List[tdb.CommentRow]
    attachments: List[tdb.AttachmentRow]


@dataclass(frozen=True)
class DeleteTicketResult:
    ok: bool
    hard: bool
    orphaned_file_source_ids: List[str]


# ENUM APPLICATIVI (espandibili senza migration

TICKET_CATEGORIES: Tuple[str, ...] = (
    "auth",
    "pipeline",
    "ui",
    "performance",
    "data_quality",
    "deploy",
    "other",
)

TICKET_KINDS: Tuple[str, ...] = ("tech_debt", "user_support")
TICKET_SEVERITIES: Tuple[str, ...] = ("low", "medium", "high", "critical")
TICKET_STATUSES: Tuple[str, ...] = (
    "open", "in_progress", "waiting_user", "resolved", "closed", "reopened",
)
TICKET_STATUSES_ACTIVE: Tuple[str, ...] = (
    "open", "in_progress", "waiting_user", "reopened",
)

# Ruoli che possono creare tech_debt (admin/superadmin, non utenti finali
_ROLES_TECH_DEBT_CREATE = {"admin", "superadmin"}
_ROLES_ADMIN = {"admin", "superadmin"}


# NOTIFIER HOOK (email-ready)


class TicketNotifier(Protocol):
    """Contratto strutturale per notifiche ticket.

    Ogni metodo e' best-effort: eccezioni vengono loggate ma non bloccano
    il flusso principale (la persistenza del ticket e' transazione gia'
    committata). Mai far fallire una write DB per una notifica persa.
    """

    def on_ticket_created(self, ticket: tdb.TicketRow, actor: CurrentUser) -> None:
        ...

    def on_status_changed(
        self,
        ticket: tdb.TicketRow,
        old_status: str,
        _new_status: str,
        actor: CurrentUser,
    ) -> None:
        ...

    def on_comment_added(
        self,
        ticket: tdb.TicketRow,
        comment: tdb.CommentRow,
        actor: CurrentUser,
    ) -> None:
        ...

    def on_assignee_changed(
        self,
        ticket: tdb.TicketRow,
        old_assignee: Optional[str],
        _new_assignee: Optional[str],
        actor: CurrentUser,
    ) -> None:
        ...


class NoopTicketNotifier:
    """Default notifier esplicito: nessun side effect se non registrato."""

    def on_ticket_created(self, ticket: tdb.TicketRow, actor: CurrentUser) -> None:
        pass

    def on_status_changed(
        self,
        ticket: tdb.TicketRow,
        old_status: str,
        _new_status: str,
        actor: CurrentUser,
    ) -> None:
        pass

    def on_comment_added(
        self,
        ticket: tdb.TicketRow,
        comment: tdb.CommentRow,
        actor: CurrentUser,
    ) -> None:
        pass

    def on_assignee_changed(
        self,
        ticket: tdb.TicketRow,
        old_assignee: Optional[str],
        _new_assignee: Optional[str],
        actor: CurrentUser,
    ) -> None:
        pass


# Singleton default no-op. Sostituibile a startup in main.py.
_notifier: TicketNotifier = NoopTicketNotifier()


def set_notifier(notifier: TicketNotifier) -> None:
    """Registra il notifier custom per email/Slack/Teams."""
    global _notifier
    _notifier = notifier


def _fire_notification(method_name: str, *args, **kwargs) -> None:
    """Invoca un metodo del notifier con try/except: notifica best-effort."""
    try:
        method = getattr(_notifier, method_name, None)
        if method:
            method(*args, **kwargs)
    except Exception as e:
        logger.warning(
            f"Ticket notifier.{method_name} fallito (non bloccante): {e}",
            op="ticket_notify",
        )


# VALIDAZIONE INPUT


def _validate_create_input(
    *,
    kind: str,
    title: str,
    severity: str,
    category: str,
    actor: CurrentUser,
) -> None:
    if kind not in TICKET_KINDS:
        raise HTTPException(400, f"kind non valido: {kind}. Validi: {TICKET_KINDS}")
    if kind == "tech_debt" and actor.role not in _ROLES_TECH_DEBT_CREATE:
        raise HTTPException(
            403,
            "Solo admin/superadmin possono creare ticket 'tech_debt'. "
            "Gli utenti usano 'user_support'.",
        )
    if not title or len(title.strip()) < 3:
        raise HTTPException(400, "title deve essere lungo almeno 3 caratteri")
    if len(title) > 200:
        raise HTTPException(400, "title troppo lungo (max 200 char)")
    if severity not in TICKET_SEVERITIES:
        raise HTTPException(400, f"severity non valida: {severity}. Validi: {TICKET_SEVERITIES}")
    if category not in TICKET_CATEGORIES:
        raise HTTPException(400, f"category non valida: {category}. Validi: {TICKET_CATEGORIES}")


# RBAC HELPERS


def _is_admin(actor: CurrentUser) -> bool:
    return actor.role in _ROLES_ADMIN


def _can_view(ticket: tdb.TicketRow, actor: CurrentUser) -> bool:
    """True se l'attore puo' vedere questo ticket."""
    if actor.role == "superadmin":
        return True
    if actor.role in _ROLES_ADMIN:
        return ticket.group_id == actor.group_id
    # viewer/user: solo i propri
    return ticket.created_by == actor.user_id


def _can_edit_privileged_fields(ticket: tdb.TicketRow, actor: CurrentUser) -> bool:
    """True se l'attore puo' modificare status/assignee/severity/category."""
    if actor.role == "superadmin":
        return True
    if actor.role in _ROLES_ADMIN:
        return ticket.group_id == actor.group_id
    return False


def check_access(ticket: tdb.TicketRow | None, actor: CurrentUser) -> tdb.TicketRow:
    """Raise 404 se ticket inesistente, 403 se non autorizzato, else ritorna ticket."""
    if not ticket:
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.TICKET_NOT_FOUND,
                "message": "Ticket non trovato",
            },
        )
    if not _can_view(ticket, actor):
        # 404 per non leakare esistenza: stesso codice/message del
        # ticket realmente assente, l'access-denied resta indistinguibile
        # dal not-found dal punto di vista del client
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.TICKET_NOT_FOUND,
                "message": "Ticket non trovato",
            },
        )
    return ticket


# OPERAZIONI PUBBLICHE


def create_ticket(
    *,
    kind: str,
    title: str,
    description: str,
    severity: str,
    category: str,
    actor: CurrentUser,
    target_group_id: Optional[str] = None,
    assignee: Optional[str] = None,
) -> tdb.TicketRow:
    """Crea un ticket. target_group_id e' usato solo da superadmin per
    creare tech_debt cross-group; altrimenti viene forzato al group
    dell'attore."""
    _validate_create_input(
        kind=kind, title=title, severity=severity, category=category, actor=actor,
    )

    # group_id enforcement
    if actor.role == "superadmin" and target_group_id:
        group_id = target_group_id
    else:
        group_id = actor.group_id

    # Assignee: solo admin/superadmin possono pre-assegnare
    if assignee and not _is_admin(actor):
        raise HTTPException(403, "Solo admin/superadmin possono assegnare un ticket")

    ticket = tdb.create_ticket(
        kind=kind,
        title=title.strip(),
        description=description or "",
        severity=severity,
        category=category,
        created_by=actor.user_id,
        group_id=group_id,
        assignee=assignee,
    )
    logger.info(
        f"Ticket creato: {ticket.id} kind={kind} severity={severity} "
        f"by={actor.username}",
        op="ticket_create",
    )
    _fire_notification("on_ticket_created", ticket, actor)
    return ticket


def list_tickets_for(
    actor: CurrentUser,
    *,
    kind: Optional[str] = None,
    status_in: Optional[List[str]] = None,
    severity_in: Optional[List[str]] = None,
    assignee: Optional[str] = None,
    mine: bool = False,
    limit: int = 500,
) -> List[tdb.TicketRow]:
    """Lista ticket filtrata secondo RBAC dell'attore."""
    if actor.role == "esecutore":
        raise HTTPException(403, "Gli utenti esecutore non hanno accesso ai ticket")

    # Filtro scope: superadmin → cross-group; admin → group_id; user → created_by
    group_filter = None
    created_by_filter = None
    if actor.role == "superadmin":
        pass  # cross-group
    elif actor.role in _ROLES_ADMIN:
        group_filter = actor.group_id
    else:
        created_by_filter = actor.user_id

    # `mine` restringe ulteriormente: se TRUE, l'utente chiede solo i propri
    # per admin filtra per assignee=me; per user e' gia' implicito da
    # created_by_filter ma manteniamo coerenza esponendo il flag
    if mine:
        if actor.role in _ROLES_ADMIN:
            assignee = actor.user_id
        else:
            created_by_filter = actor.user_id

    return tdb.list_tickets(
        group_id=group_filter,
        created_by=created_by_filter,
        assignee=assignee,
        status_in=status_in,
        kind=kind,
        severity_in=severity_in,
        limit=limit,
    )


def get_ticket_with_details(ticket_id: str, actor: CurrentUser) -> TicketDetails:
    """Dettaglio + commenti + allegati."""
    ticket = check_access(tdb.get_ticket(ticket_id), actor)
    return TicketDetails(
        ticket=ticket,
        comments=tdb.list_comments(ticket_id),
        attachments=tdb.list_attachments(ticket_id),
    )


def update_ticket(
    ticket_id: str,
    *,
    actor: CurrentUser,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    category: Optional[str] = None,
    assignee: Any = "__UNSET__",
    title: Optional[str] = None,
    description: Optional[str] = None,
) -> tdb.TicketRow:
    """Update con RBAC.

    Regole:
      - user/viewer: possono riaprire un proprio ticket (resolved/closed
        → reopened) e aggiornare description/title dei propri. NON
        possono toccare severity, assignee, category.
      - admin/superadmin: full update entro lo scope autorizzato.
    """
    ticket = check_access(tdb.get_ticket(ticket_id), actor)
    is_privileged = _can_edit_privileged_fields(ticket, actor)

    # Enforcement campi privilegiati
    privileged_fields = {
        "severity": severity,
        "category": category,
    }
    touches_assignee = assignee != "__UNSET__"
    for fname, fval in privileged_fields.items():
        if fval is not None and not is_privileged:
            raise HTTPException(403, f"Solo admin/superadmin possono modificare '{fname}'")
    if touches_assignee and not is_privileged:
        raise HTTPException(403, "Solo admin/superadmin possono assegnare un ticket")

    # Transizione status: user puo' solo riaprire i PROPRI
    if status is not None and not is_privileged:
        if ticket.created_by != actor.user_id:
            raise HTTPException(403, "Non puoi cambiare lo status di un ticket altrui")
        if status != "reopened":
            raise HTTPException(403, "Come utente puoi solo riaprire un tuo ticket chiuso")
        if ticket.status not in ("resolved", "closed"):
            raise HTTPException(
                400,
                f"Non puoi riaprire un ticket in stato '{ticket.status}'",
            )

    # Validazione enum se passati
    if status is not None and status not in TICKET_STATUSES:
        raise HTTPException(400, f"status non valido: {status}")
    if severity is not None and severity not in TICKET_SEVERITIES:
        raise HTTPException(400, f"severity non valida: {severity}")
    if category is not None and category not in TICKET_CATEGORIES:
        raise HTTPException(400, f"category non valida: {category}")
    if title is not None:
        if len(title.strip()) < 3:
            raise HTTPException(400, "title troppo corto")
        if len(title) > 200:
            raise HTTPException(400, "title troppo lungo")

    old_status = ticket.status
    old_assignee = ticket.assignee

    updated = tdb.update_ticket(
        ticket_id,
        status=status,
        severity=severity,
        category=category,
        assignee=assignee,
        title=title,
        description=description,
    )
    if updated is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.TICKET_NOT_FOUND,
                "message": "Ticket non trovato",
            },
        )

    # Notifiche
    if status is not None and status != old_status:
        _fire_notification("on_status_changed", updated, old_status, status, actor)
    if touches_assignee and old_assignee != assignee:
        _fire_notification(
            "on_assignee_changed", updated, old_assignee, assignee, actor,
        )

    logger.info(
        f"Ticket {ticket_id} aggiornato by={actor.username}: "
        f"status={old_status}->{status or old_status}, "
        f"assignee={old_assignee}->{assignee if touches_assignee else old_assignee}",
        op="ticket_update",
    )
    return updated


def add_comment(ticket_id: str, body: str, actor: CurrentUser) -> tdb.CommentRow:
    """Aggiunge un commento. Il creatore del ticket e admin/superadmin
    del gruppo possono sempre commentare; gli altri utenti no."""
    ticket = check_access(tdb.get_ticket(ticket_id), actor)
    if not body or not body.strip():
        raise HTTPException(400, "Il commento non puo' essere vuoto")
    if len(body) > 10000:
        raise HTTPException(400, "Commento troppo lungo (max 10000 char)")

    comment = tdb.add_comment(ticket_id, actor.user_id, body.strip())
    _fire_notification("on_comment_added", ticket, comment, actor)
    return comment


def delete_ticket(
    ticket_id: str, actor: CurrentUser, *, hard: bool = False,
) -> DeleteTicketResult:
    """Cancellazione del ticket.

    Default `hard=False`: soft delete (set `deleted_at`). Audit trail
    preservato (commenti + allegati restano in DB).

    `hard=True`: purge definitivo via FK cascade. Riservato a `superadmin`
    (admin di gruppo non basta — la perdita dati e' irrecuperabile).
    Ritorna gli attachment id da cleanare lato caller (best-effort).
    """
    ticket = check_access(tdb.get_ticket(ticket_id), actor)
    if not _can_edit_privileged_fields(ticket, actor):
        raise HTTPException(403, "Solo admin/superadmin possono eliminare ticket")

    if hard:
        if actor.role != "superadmin":
            raise HTTPException(403, "Hard delete riservato a superadmin")
        # Snapshot file_source_id PRIMA del delete (cascade rimuove i link
        attachments = tdb.list_attachments(ticket_id)
        file_source_ids = [a.file_source_id for a in attachments]
        ok = tdb.delete_ticket(ticket_id)
        if not ok:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.TICKET_NOT_FOUND,
                    "message": "Ticket non trovato",
                },
            )
        return DeleteTicketResult(
            ok=True,
            hard=True,
            orphaned_file_source_ids=file_source_ids,
        )

    # Soft delete (default): nessun cleanup blob (gli allegati restano
    # accessibili per recovery/audit, file_source non orfanato
    ok = tdb.soft_delete_ticket(ticket_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.TICKET_NOT_FOUND,
                "message": "Ticket non trovato",
            },
        )
    return DeleteTicketResult(ok=True, hard=False, orphaned_file_source_ids=[])


def _category_display() -> List[Dict[str, str]]:
    """Ritorna la lista categorie con label human-readable (per dropdown FE)."""
    labels = {
        "auth": "Autenticazione",
        "pipeline": "Pipeline / esecuzioni",
        "ui": "Interfaccia",
        "performance": "Performance",
        "data_quality": "Qualità dati",
        "deploy": "Deploy / infrastruttura",
        "other": "Altro",
    }
    return [{"value": c, "label": labels.get(c, c)} for c in TICKET_CATEGORIES]
