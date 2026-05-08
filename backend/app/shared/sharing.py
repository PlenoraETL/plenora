"""Compatibility re-exports for auth-owned sharing policy and CRUD helpers."""

from __future__ import annotations

from app.core.auth.shares import (
    add_resource_share,
    list_resource_shares,
    remove_resource_share,
    set_resource_shares,
)
from app.core.auth.visibility import (
    MAGIC_ALL,
    ShareableResourceOwner,
    _SHAREABLE,
    _validate_table,
    build_visible_where_clause,
    fk_column_for,
    is_resource_visible,
    is_shareable,
    load_shareable_resource_owner,
    share_table_for,
    visible_resource_ids,
)

__all__ = [
    "MAGIC_ALL",
    "ShareableResourceOwner",
    "_SHAREABLE",
    "_validate_table",
    "add_resource_share",
    "build_visible_where_clause",
    "fk_column_for",
    "is_resource_visible",
    "is_shareable",
    "list_resource_shares",
    "load_shareable_resource_owner",
    "remove_resource_share",
    "set_resource_shares",
    "share_table_for",
    "visible_resource_ids",
]
