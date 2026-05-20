"""Auth-owned visibility policy for shareable resources."""

from __future__ import annotations

from typing import Optional, cast
from typing_extensions import TypedDict

from app.core.auth import share_store


MAGIC_ALL = "*"

_SHAREABLE: dict[str, tuple[str, str]] = {
    "connections": ("connection_shares", "connection_id"),
    "datasources": ("datasource_shares", "datasource_id"),
    "eseguibili": ("eseguibile_shares", "eseguibile_id"),
    "api_connections": ("api_connection_shares", "api_connection_id"),
    "arcgis_connections": ("arcgis_connection_shares", "arcgis_connection_id"),
    "file_sources": ("file_source_shares", "file_source_id"),
}


class ShareableResourceOwner(TypedDict):
    id: str
    group_id: str | None


def _validate_table(resource_table: str) -> tuple[str, str]:
    if resource_table not in _SHAREABLE:
        raise ValueError(
            f"Tabella '{resource_table}' non e' shareable. "
            f"Validi: {list(_SHAREABLE)}"
        )
    return _SHAREABLE[resource_table]


def is_shareable(resource_table: str) -> bool:
    return resource_table in _SHAREABLE


def load_shareable_resource_owner(
    resource_table: str,
    resource_id: str,
) -> ShareableResourceOwner | None:
    """Load the owner group row for a shareable resource."""
    _validate_table(resource_table)
    row = share_store.fetch_shareable_resource_owner(resource_table, resource_id)
    return cast(ShareableResourceOwner, row) if row else None


def visible_resource_ids(
    resource_table: str,
    user_group_id: str,
    is_superadmin: bool = False,
) -> set[str]:
    """Return ids from a resource table visible to a user group."""
    share_table, fk = _validate_table(resource_table)
    return share_store.fetch_visible_resource_ids(
        resource_table,
        share_table,
        fk,
        user_group_id,
        MAGIC_ALL,
        is_superadmin=is_superadmin,
    )


def is_resource_visible(
    resource_table: str,
    resource_id: str,
    user_group_id: str,
    is_superadmin: bool = False,
    resource_owner_group_id: Optional[str] = None,
) -> bool:
    """Return whether a resource is owned by or shared with a user group."""
    if is_superadmin:
        return True

    share_table, fk = _validate_table(resource_table)
    return share_store.check_resource_visible(
        resource_table,
        share_table,
        fk,
        resource_id,
        user_group_id,
        MAGIC_ALL,
        resource_owner_group_id=resource_owner_group_id,
    )
