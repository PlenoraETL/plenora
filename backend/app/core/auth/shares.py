"""Auth-owned CRUD helpers for cross-group sharing rows."""

from __future__ import annotations

from typing import Iterable, Mapping

from app.core.auth import share_store
from app.core.auth.visibility import _validate_table


def list_resource_shares(resource_table: str, resource_id: str) -> list[str]:
    """
    Return the group ids a resource is shared with.
    Includes "*" when shared globally.
    """
    share_table, fk = _validate_table(resource_table)
    return share_store.list_resource_share_group_ids(share_table, fk, resource_id)


def resource_owner_groups(
    resource_table: str,
    resource_ids: Iterable[str],
) -> Mapping[str, str | None]:
    """Owner group of many resources of one shareable table (id -> group_id)."""
    _validate_table(resource_table)
    return share_store.fetch_resource_owner_groups(resource_table, resource_ids)


def resource_shares_by_id(
    resource_table: str,
    resource_ids: Iterable[str],
) -> Mapping[str, list[str]]:
    """Share group ids of many resources (id -> group ids, "*" = all groups).

    Resources without share rows are absent from the result.
    """
    share_table, fk = _validate_table(resource_table)
    return share_store.fetch_resource_share_group_ids_by_id(share_table, fk, resource_ids)


def add_resource_share(
    resource_table: str,
    resource_id: str,
    group_id: str,
) -> bool:
    """
    Add one sharing row idempotently.
    Returns True if the row was added, False if it already existed.
    """
    share_table, fk = _validate_table(resource_table)
    return share_store.insert_resource_share(share_table, fk, resource_id, group_id)


def remove_resource_share(
    resource_table: str,
    resource_id: str,
    group_id: str,
) -> bool:
    """Remove one sharing row. Returns True if a row was removed."""
    share_table, fk = _validate_table(resource_table)
    return share_store.delete_resource_share(share_table, fk, resource_id, group_id)


def set_resource_shares(
    resource_table: str,
    resource_id: str,
    group_ids: Iterable[str],
) -> None:
    """
    Replace all sharing rows for a resource.
    Pass ["*"] for global sharing, [] to remove all shares.
    """
    share_table, fk = _validate_table(resource_table)
    share_store.replace_resource_shares(share_table, fk, resource_id, group_ids)
