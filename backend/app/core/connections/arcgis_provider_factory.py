"""Neutral ArcGIS provider factory for connection consumers."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from app.shared.connections.arcgis_provider import ArcGISProviderProtocol

if TYPE_CHECKING:
    from app.core.features.arcgis_client import HttpClientProtocol


def create_arcgis_provider(
    http_client: Optional["HttpClientProtocol"] = None,
) -> ArcGISProviderProtocol:
    """Create the canonical ArcGIS provider instance.

    Connection-oriented code should import this neutral factory instead of
    constructing the concrete feature provider directly.
    """
    from app.core.features.arcgis_client import ArcGISClient

    return ArcGISClient(http_client=http_client)


__all__ = ["create_arcgis_provider"]
