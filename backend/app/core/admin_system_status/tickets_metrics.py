"""Ticket metrics adapter for admin system status."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActiveTicketMetrics:
    total_active: int
    by_severity: dict[str, int]


class TicketsMetricsUnavailable(RuntimeError):
    """Raised when the ticket metrics dependencies are not importable."""


def get_active_ticket_metrics() -> ActiveTicketMetrics:
    """Return cross-group active ticket counters for the superadmin dashboard."""
    try:
        from app.core.tickets.service import TICKET_STATUSES_ACTIVE
        from app.db import tickets_db_service as tdb
    except Exception as exc:
        raise TicketsMetricsUnavailable(str(exc)) from exc

    active_statuses = list(TICKET_STATUSES_ACTIVE)
    total_active = tdb.count_tickets(status_in=active_statuses)
    by_severity_raw = tdb.counts_by_severity(status_in=active_statuses)
    return ActiveTicketMetrics(
        total_active=total_active,
        by_severity={
            str(key): int(value or 0)
            for key, value in by_severity_raw.items()
        },
    )
