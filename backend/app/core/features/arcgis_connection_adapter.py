"""ArcGIS connection resolver - thin adapter for IMDF validation.

BE-GO-READY-ARCH-059-W4: narrows the Features domain surface that
``imdf/validator.py`` needs. The validator only receives
(ArcGISConnectionInfo, ArcGISProviderProtocol) - it no longer imports
the full FeaturesService class.

This module remains in ``app.core.features`` because the DB lookup
(``get_connection_db``) lives there. The goal is not to move DB logic
into shared but to present a minimal seam: one function, typed return,
no FeaturesService class exposure.

ADR-0023 / CONNECTION_ENGINE: validator dependency surface before this
batch was ``FeaturesService`` (CRUD + browse + query + write). After
this batch it is ``resolve_arcgis_connection`` only.
"""

from __future__ import annotations

from typing import Optional, Tuple

from app.core.features.arcgis_client import ArcGISConnectionInfo, create_client
from app.core.features.features_service import get_connection_db
from app.shared.connections.arcgis_provider import ArcGISProviderProtocol


__all__ = ["resolve_arcgis_connection"]


def resolve_arcgis_connection(
    connection_id: str,
) -> Optional[Tuple[ArcGISConnectionInfo, ArcGISProviderProtocol]]:
    """Resolve a connection_id to (ArcGISConnectionInfo, client).

    Returns ``None`` if the connection is not found in the DB.  The
    client is typed against ``ArcGISProviderProtocol`` so callers
    (validator.py) remain decoupled from the concrete ``ArcGISClient``
    class.

    Token cache: each call creates a fresh ``ArcGISClient`` instance.
    The validator is a short-lived read-only path; the minor extra
    token acquisition is acceptable.  If token-cache sharing becomes
    a concern, the caller can inject an existing client via the
    ``ArcGISProviderProtocol`` type directly.
    """
    conn_pyd = get_connection_db(connection_id)
    if conn_pyd is None:
        return None

    conn_info = ArcGISConnectionInfo(
        server_url=conn_pyd.server_url,
        server_type=conn_pyd.server_type,
        auth_type=conn_pyd.auth_type,
        username=conn_pyd.username,
        password=conn_pyd.password,
        client_id=conn_pyd.client_id,
        client_secret=conn_pyd.client_secret,
        token_url=conn_pyd.token_url or "",
    )
    client: ArcGISProviderProtocol = create_client()
    return conn_info, client
