from __future__ import annotations

from app.shared.error_codes import TICKET_NOT_FOUND

KNOWN_DOMAIN_CODES: frozenset[str] = frozenset({TICKET_NOT_FOUND})
