"""
ArcGIS connection resolver for provider consumers.

This module stays under ``app.core.connections`` because resolving a stored
ArcGIS connection requires a DB lookup. ``app.shared.connections`` remains a
leaf package for pure contracts and must not import DB/core modules.
"""

from __future__ import annotations

from typing import Optional, Tuple

from app.core.connections import db as connection_db
from app.core.connections.arcgis_provider_factory import create_arcgis_provider
from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.shared.connections.arcgis_provider import ArcGISProviderProtocol
from app.infrastructure.security.crypto import decrypt as _decrypt


__all__ = ["resolve_arcgis_connection"]


def _row_to_connection_info(row: connection_db.ArcGISConnectionRow) -> ArcGISConnectionInfo:
    return ArcGISConnectionInfo(
        server_url=row.server_url,
        server_type=row.server_type,
        auth_type=row.auth_type,
        username=row.username,
        password=_decrypt(row.password) or "",
        client_id=row.client_id,
        client_secret=_decrypt(row.client_secret) or "",
        token_url=row.token_url,
    )


def resolve_arcgis_connection(
    connection_id: str,
) -> Optional[Tuple[ArcGISConnectionInfo, ArcGISProviderProtocol]]:
    """Resolve an ArcGIS connection id to connection info plus provider client.

    Returns ``None`` if the stored connection is not found. The returned client
    is typed against ``ArcGISProviderProtocol`` so consumers do not depend on
    the concrete ``ArcGISClient`` class.
    """
    row = connection_db.get_arcgis_connection_row(connection_id)
    if row is None:
        return None

    conn_info = _row_to_connection_info(row)
    client: ArcGISProviderProtocol = create_arcgis_provider()
    return conn_info, client
