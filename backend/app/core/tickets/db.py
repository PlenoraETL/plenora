"""Persistence adapter for the tickets core domain.

This module is the only core/tickets boundary that knows about the legacy
database facade. Domain services and routers import this adapter instead of
depending on ``app.db.tickets_db_service`` directly.
"""

from __future__ import annotations

from app.db import tickets_db_service as _tdb

TicketRow = _tdb.TicketRow
CommentRow = _tdb.CommentRow
AttachmentRow = _tdb.AttachmentRow

create_ticket = _tdb.create_ticket
get_ticket = _tdb.get_ticket
list_tickets = _tdb.list_tickets
update_ticket = _tdb.update_ticket
count_tickets = _tdb.count_tickets
counts_by_severity = _tdb.counts_by_severity
add_comment = _tdb.add_comment
list_comments = _tdb.list_comments
link_attachment = _tdb.link_attachment
list_attachments = _tdb.list_attachments
delete_attachment = _tdb.delete_attachment
get_attachment = _tdb.get_attachment
soft_delete_ticket = _tdb.soft_delete_ticket
delete_ticket = _tdb.delete_ticket

