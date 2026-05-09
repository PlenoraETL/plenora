"""Ticket metrics adapter for admin system status."""

from __future__ import annotations

from dataclasses import dataclass

try:
    from app.core.tickets.service import TICKET_STATUSES_ACTIVE
    from app.core.tickets import db as tickets_db
    _TICKETS_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - exercised through import-failure contracts
    TICKET_STATUSES_ACTIVE = ()
    tickets_db = None
    _TICKETS_IMPORT_ERROR = exc


@dataclass(frozen=True)
class ActiveTicketMetrics:
    total_active: int
    by_severity: dict[str, int]


class TicketsMetricsUnavailable(RuntimeError):
    """Raised when the ticket metrics dependencies are not importable."""


def get_active_ticket_metrics() -> ActiveTicketMetrics:
    """Return cross-group active ticket counters for the superadmin dashboard."""
    if _TICKETS_IMPORT_ERROR is not None or tickets_db is None:
        raise TicketsMetricsUnavailable(str(_TICKETS_IMPORT_ERROR)) from _TICKETS_IMPORT_ERROR

    active_statuses = list(TICKET_STATUSES_ACTIVE)
    total_active = tickets_db.count_tickets(status_in=active_statuses)
    by_severity_raw = tickets_db.counts_by_severity(status_in=active_statuses)
    return ActiveTicketMetrics(
        total_active=total_active,
        by_severity={
            str(key): int(value or 0)
            for key, value in by_severity_raw.items()
        },
    )
