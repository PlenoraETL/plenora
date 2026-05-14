"""Neutral ArcGIS provider factory for connection consumers."""

from __future__ import annotations

from typing import Optional

from app.core.connections.arcgis_http_client_protocol import (
    ArcGISProviderHttpClientProtocol,
)
from app.shared.connections.arcgis_provider import ArcGISProviderProtocol


def create_arcgis_provider(
    http_client: Optional[ArcGISProviderHttpClientProtocol] = None,
) -> ArcGISProviderProtocol:
    """Create the canonical ArcGIS provider instance.

    Connection-oriented code should import this neutral factory instead of
    constructing the concrete feature provider directly.
    """
    from app.core.features.arcgis_client import ArcGISClient

    return ArcGISClient(http_client=http_client)


__all__ = ["ArcGISProviderHttpClientProtocol", "create_arcgis_provider"]
