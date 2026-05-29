from __future__ import annotations

from app.shared.error_codes import DATASOURCE_IN_USE, DATASOURCE_NOT_FOUND

KNOWN_DOMAIN_CODES: frozenset[str] = frozenset({
    DATASOURCE_IN_USE,
    DATASOURCE_NOT_FOUND,
})
