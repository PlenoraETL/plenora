"""
DB service layer — tickets + comments + attachments.

Tabelle in `plenora.*`:
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db.pg_connection import (
    fetchone, fetchall, execute, transaction,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


# TICKETS — CRUD


def create_ticket(
    *,
    kind: str,
    title: str,
    description: str,
    severity: str,
    category: str,
    created_by: str,
    group_id: str,
    status: str = "open",
    assignee: Optional[str] = None,
) -> Dict[str, Any]:
    """Crea nuovo ticket. Ritorna la row appena creata."""
    ticket_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT INTO tickets (id, kind, title, description, severity, status, "
        "category, created_by, assignee, group_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (ticket_id, kind, title, description, severity, status,
         category, created_by, assignee, group_id, now, now),
    )
    return get_ticket(ticket_id)


def get_ticket(
    ticket_id: str, *, include_deleted: bool = False,
) -> Optional[Dict[str, Any]]:
    """Recupera ticket per id. None se non esiste o e' soft-deleted.

    `include_deleted=True`: bypass del filtro `deleted_at IS NULL`.
    Riservato a operazioni di audit / superadmin recovery.
    """
    if include_deleted:
        return fetchone("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    return fetchone(
        "SELECT * FROM tickets WHERE id = ? AND deleted_at IS NULL",
        (ticket_id,),
    )


def list_tickets(
    *,
    group_id: Optional[str] = None,
    created_by: Optional[str] = None,
    assignee: Optional[str] = None,
    status_in: Optional[List[str]] = None,
    kind: Optional[str] = None,
    severity_in: Optional[List[str]] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Lista ticket con filtri componibili.

    Filtri:
      - group_id: solo ticket di questo gruppo (None = cross-group, solo
        superadmin lo passa None)
      - created_by: solo ticket aperti da questo utente
      - assignee: solo ticket assegnati a questo utente
      - status_in: uno o piu' stati (default = tutti)
      - kind: 'tech_debt' | 'user_support'
      - severity_in: uno o piu' livelli

    Ordine: updated_at DESC (cosa e' cambiato di recente sta sopra).
    """
    # Filtro soft-delete sempre attivo: chi vuole vedere i cancellati
    # passa per get_ticket(include_deleted=True) puntuale
    clauses: List[str] = ["deleted_at IS NULL"]
    params: List[Any] = []

    if group_id is not None:
        clauses.append("group_id = ?")
        params.append(group_id)
    if created_by is not None:
        clauses.append("created_by = ?")
        params.append(created_by)
    if assignee is not None:
        clauses.append("assignee = ?")
        params.append(assignee)
    if kind is not None:
        clauses.append("kind = ?")
        params.append(kind)
    if status_in:
        placeholders = ",".join(["?"] * len(status_in))
        clauses.append(f"status IN ({placeholders})")
        params.extend(status_in)
    if severity_in:
        placeholders = ",".join(["?"] * len(severity_in))
        clauses.append(f"severity IN ({placeholders})")
        params.extend(severity_in)

    where = f"WHERE {' AND '.join(clauses)}"
    sql = f"SELECT * FROM tickets {where} ORDER BY updated_at DESC LIMIT ?"
    params.append(int(limit))
    return fetchall(sql, tuple(params))


def update_ticket(
    ticket_id: str,
    *,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    category: Optional[str] = None,
    assignee: Optional[str] = "__UNSET__",
    title: Optional[str] = None,
    description: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Update parziale. Passare `assignee=None` per rimuovere l'assegnazione
    (differente da default sentinel `"__UNSET__"` che significa "non toccare").
    Auto-aggiorna `updated_at` e popola `resolved_at` quando si passa a
    status='resolved' la prima volta.

    Ritorna la row aggiornata, None se ticket inesistente.
    """
    current = get_ticket(ticket_id)
    if not current:
        return None

    fields: Dict[str, Any] = {}
    if status is not None:
        fields["status"] = status
    if severity is not None:
        fields["severity"] = severity
    if category is not None:
        fields["category"] = category
    if assignee != "__UNSET__":
        fields["assignee"] = assignee  # anche None e' valido (unassign
    if title is not None:
        fields["title"] = title
    if description is not None:
        fields["description"] = description

    if not fields:
        return current

    now = datetime.now(timezone.utc).isoformat()
    fields["updated_at"] = now

    # resolved_at: popolato solo alla PRIMA transizione verso 'resolved'
    # preservato poi (anche se il ticket viene riaperto e richiuso, il
    # primo resolved_at e' sufficiente per metriche time-to-first-resolution
    if status == "resolved" and not current.get("resolved_at"):
        fields["resolved_at"] = now

    set_parts = []
    params: List[Any] = []
    for k, v in fields.items():
        set_parts.append(f"{k} = ?")
        params.append(v)
    params.append(ticket_id)
    execute(
        f"UPDATE tickets SET {', '.join(set_parts)} WHERE id = ?",
        tuple(params),
    )
    return get_ticket(ticket_id)


def count_tickets(
    *,
    group_id: Optional[str] = None,
    assignee: Optional[str] = None,
    status_in: Optional[List[str]] = None,
    kind: Optional[str] = None,
) -> int:
    """Conta ticket matching i filtri. Usato per badge sidebar e card dashboard.
    Esclude i soft-deleted (per definizione: il badge non deve contare ticket cancellati)."""
    clauses: List[str] = ["deleted_at IS NULL"]
    params: List[Any] = []

    if group_id is not None:
        clauses.append("group_id = ?")
        params.append(group_id)
    if assignee is not None:
        clauses.append("assignee = ?")
        params.append(assignee)
    if kind is not None:
        clauses.append("kind = ?")
        params.append(kind)
    if status_in:
        placeholders = ",".join(["?"] * len(status_in))
        clauses.append(f"status IN ({placeholders})")
        params.extend(status_in)

    where = f"WHERE {' AND '.join(clauses)}"
    row = fetchone(f"SELECT COUNT(*) AS cnt FROM tickets {where}", tuple(params))
    return int(row["cnt"]) if row else 0


def counts_by_severity(
    *,
    group_id: Optional[str] = None,
    status_in: Optional[List[str]] = None,
) -> Dict[str, int]:
    """Breakdown counts per severity (per card dashboard). Esclude soft-deleted."""
    clauses: List[str] = ["deleted_at IS NULL"]
    params: List[Any] = []
    if group_id is not None:
        clauses.append("group_id = ?")
        params.append(group_id)
    if status_in:
        placeholders = ",".join(["?"] * len(status_in))
        clauses.append(f"status IN ({placeholders})")
        params.extend(status_in)

    where = f"WHERE {' AND '.join(clauses)}"
    rows = fetchall(
        f"SELECT severity, COUNT(*) AS cnt FROM tickets {where} "
        f"GROUP BY severity",
        tuple(params),
    )
    out = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    for r in rows:
        out[r["severity"]] = int(r["cnt"])
    return out


# COMMENTS


def add_comment(ticket_id: str, author_id: str, body: str) -> Dict[str, Any]:
    """Aggiunge un commento + tocca updated_at del ticket."""
    cid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ticket_comments (id, ticket_id, author_id, body, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (cid, ticket_id, author_id, body, now),
            )
            cur.execute(
                "UPDATE tickets SET updated_at = %s WHERE id = %s",
                (now, ticket_id),
            )
    return {
        "id": cid,
        "ticket_id": ticket_id,
        "author_id": author_id,
        "body": body,
        "created_at": now,
    }


def list_comments(ticket_id: str) -> List[Dict[str, Any]]:
    return fetchall(
        "SELECT c.*, u.username AS author_username "
        "FROM ticket_comments c "
        "LEFT JOIN users u ON u.id = c.author_id "
        "WHERE c.ticket_id = ? ORDER BY c.created_at ASC",
        (ticket_id,),
    )


# ATTACHMENTS


def link_attachment(
    ticket_id: str,
    file_source_id: str,
    uploaded_by: str,
) -> Dict[str, Any]:
    """Registra un file_source come allegato del ticket.
    Il file_source deve essere gia' stato creato prima (via file_service)."""
    aid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT INTO ticket_attachments (id, ticket_id, file_source_id, uploaded_by, uploaded_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (aid, ticket_id, file_source_id, uploaded_by, now),
    )
    # Touch ticket updated_at
    execute(
        "UPDATE tickets SET updated_at = ? WHERE id = ?",
        (now, ticket_id),
    )
    return {
        "id": aid,
        "ticket_id": ticket_id,
        "file_source_id": file_source_id,
        "uploaded_by": uploaded_by,
        "uploaded_at": now,
    }


def list_attachments(ticket_id: str) -> List[Dict[str, Any]]:
    """Lista allegati del ticket join con file_sources per nome/size."""
    return fetchall(
        "SELECT a.id, a.ticket_id, a.file_source_id, a.uploaded_by, a.uploaded_at, "
        "       fs.name AS file_name, fs.original_filename, fs.format, "
        "       fs.size_bytes, u.username AS uploaded_by_username "
        "FROM ticket_attachments a "
        "JOIN file_sources fs ON fs.id = a.file_source_id "
        "LEFT JOIN users u ON u.id = a.uploaded_by "
        "WHERE a.ticket_id = ? ORDER BY a.uploaded_at ASC",
        (ticket_id,),
    )


def delete_attachment(attachment_id: str) -> bool:
    """Rimuove il link allegato. Il file_source sottostante NON viene
    cancellato da qui (responsabilita' del caller via file_service)."""
    cur = execute(
        "DELETE FROM ticket_attachments WHERE id = ?", (attachment_id,)
    )
    return cur.rowcount > 0


def get_attachment(attachment_id: str) -> Optional[Dict[str, Any]]:
    return fetchone(
        "SELECT * FROM ticket_attachments WHERE id = ?", (attachment_id,)
    )


# DELETE TICKET (cascade su comments/attachments via FK ON DELETE CASCADE


def soft_delete_ticket(ticket_id: str) -> bool:
    """Soft delete: setta `deleted_at = NOW()`. Il ticket sparisce
    dalle query CRUD (filtrate by default) ma la riga + commenti +
    allegati restano in DB per audit / recovery.

    Idempotente: chiamata su un ticket gia' soft-deleted aggiorna
    `deleted_at` al timestamp piu' recente (rare race ok).
    """
    now = datetime.now(timezone.utc).isoformat()
    cur = execute(
        "UPDATE tickets SET deleted_at = ? WHERE id = ?",
        (now, ticket_id),
    )
    return cur.rowcount > 0


def delete_ticket(ticket_id: str) -> bool:
    """Hard delete del ticket. Cascade automatico su `ticket_comments` e
    `ticket_attachments` via FK. Il cleanup dei file_source sottostanti
    e' responsabilita' del caller (loop su list_attachments prima del
    delete, perche' a cancellazione avvenuta non c'e' piu' la riga link).

    Nota: per il flusso normale usare `soft_delete_ticket`. Hard delete
    riservato a superadmin via `?hard=true` per casi GDPR / pulizia.
    """
    cur = execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
    return cur.rowcount > 0
