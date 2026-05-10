from __future__ import annotations


DATASOURCE_IN_USE = "datasource_in_use"
DATASOURCE_NOT_FOUND = "datasource_not_found"


KNOWN_DOMAIN_CODES: frozenset[str] = frozenset({
    DATASOURCE_IN_USE,
    DATASOURCE_NOT_FOUND,
})
