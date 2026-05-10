from __future__ import annotations


CONNECTION_IN_USE = "connection_in_use"
CONNECTION_NOT_FOUND = "connection_not_found"


KNOWN_DOMAIN_CODES: frozenset[str] = frozenset({
    CONNECTION_IN_USE,
    CONNECTION_NOT_FOUND,
})
