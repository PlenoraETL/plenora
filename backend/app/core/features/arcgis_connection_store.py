"""Compatibility shim for ArcGIS connection CRUD.

The canonical persistence boundary lives in
``app.core.connections.arcgis_connection_store``. Keep this module importable
for compatibility callers while preventing new Features-owned DB code from growing
here.
"""

from app.core.connections.arcgis_connection_store import (
    DEFAULT_GROUP,
    ArcGISConnectionDbRow,
    _execute,
    _fetchall,
    _fetchone,
    create_connection,
    delete_connection,
    ensure_default_group,
    generate_id,
    get_connection,
    list_connections,
    row_to_connection,
    update_connection,
)

__all__ = [
    "DEFAULT_GROUP",
    "ArcGISConnectionDbRow",
    "_execute",
    "_fetchall",
    "_fetchone",
    "create_connection",
    "delete_connection",
    "ensure_default_group",
    "generate_id",
    "get_connection",
    "list_connections",
    "row_to_connection",
    "update_connection",
]
