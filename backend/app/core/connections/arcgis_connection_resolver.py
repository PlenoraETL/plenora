"""
ArcGIS connection resolver for provider consumers.

This module stays under ``app.core.connections`` because resolving a stored
ArcGIS connection requires a DB lookup. ``app.shared.connections`` remains a
leaf package for pure contracts and must not import DB/core modules.
"""

from __future__ import annotations

from typing import Optional, Tuple

import app.db.pg_connection as db
from app.core.connections.arcgis_provider_factory import create_arcgis_provider
from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.shared.connections.arcgis_provider import ArcGISProviderProtocol
from app.shared.crypto import decrypt as _decrypt


__all__ = ["resolve_arcgis_connection"]


def _row_to_connection_info(row: dict[str, object]) -> ArcGISConnectionInfo:
    return ArcGISConnectionInfo(
        server_url=str(row["server_url"]),
        server_type=str(row.get("server_type") or "arcgis_server"),
        auth_type=str(row.get("auth_type") or "token"),
        username=str(row.get("username") or ""),
        password=_decrypt(row.get("password") or "") or "",
        client_id=str(row.get("client_id") or ""),
        client_secret=_decrypt(row.get("client_secret") or "") or "",
        token_url=str(row.get("token_url") or ""),
    )


def resolve_arcgis_connection(
    connection_id: str,
) -> Optional[Tuple[ArcGISConnectionInfo, ArcGISProviderProtocol]]:
    """Resolve an ArcGIS connection id to connection info plus provider client.

    Returns ``None`` if the stored connection is not found. The returned client
    is typed against ``ArcGISProviderProtocol`` so consumers do not depend on
    the concrete ``ArcGISClient`` class.
    """
    row = db.fetchone("SELECT * FROM arcgis_connections WHERE id = ?", (connection_id,))
    if row is None:
        return None

    conn_info = _row_to_connection_info(dict(row))
    client: ArcGISProviderProtocol = create_arcgis_provider()
    return conn_info, client
