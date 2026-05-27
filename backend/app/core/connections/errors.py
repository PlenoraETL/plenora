from __future__ import annotations


CONNECTION_IN_USE = "connection_in_use"
CONNECTION_NOT_FOUND = "connection_not_found"
CONNECTION_DUPLICATE_NAME = "connection_duplicate_name"


KNOWN_DOMAIN_CODES: frozenset[str] = frozenset({
    CONNECTION_IN_USE,
    CONNECTION_NOT_FOUND,
})


class ConnectionDuplicateNameError(ValueError):
    """Una connection con lo stesso name esiste gia' nel gruppo target.

    Il router converte in HTTP 409 con body
    `{error: CONNECTION_DUPLICATE_NAME, message: ...}`.
    """

    def __init__(self, name: str, group_id: str) -> None:
        super().__init__(
            f"Connessione '{name}' esiste gia' nel gruppo '{group_id}'"
        )
        self.name = name
        self.group_id = group_id
