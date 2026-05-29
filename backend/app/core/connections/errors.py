from __future__ import annotations

from app.shared.error_codes import (
    CONNECTION_DUPLICATE_NAME as _CONNECTION_DUPLICATE_NAME,
    CONNECTION_IN_USE,
    CONNECTION_NOT_FOUND,
)

CONNECTION_DUPLICATE_NAME = _CONNECTION_DUPLICATE_NAME

KNOWN_DOMAIN_CODES: frozenset[str] = frozenset({
    CONNECTION_IN_USE,
    CONNECTION_NOT_FOUND,
    CONNECTION_DUPLICATE_NAME,
})


class ConnectionDuplicateNameError(ValueError):
    """Una connection con lo stesso name esiste gia' nel gruppo target."""

    def __init__(self, name: str, group_id: str) -> None:
        super().__init__(
            f"Connessione '{name}' esiste gia' nel gruppo '{group_id}'"
        )
        self.name = name
        self.group_id = group_id
