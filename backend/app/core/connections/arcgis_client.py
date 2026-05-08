"""Canonical neutral import path for the concrete ArcGIS provider client.

Production consumers should prefer ``create_arcgis_provider`` and
``ArcGISProviderProtocol``. This module exists for the narrow cases that need
the concrete provider type, while ``app.core.features.arcgis_client`` remains
as the compatibility location for existing concrete-client tests and legacy
monkeypatch paths.

KEEP_COMPAT_WITH_REMOVAL_CONDITION: remove the feature-domain compatibility
path only after direct concrete-client tests and external monkeypatch/import
contracts no longer reference ``app.core.features.arcgis_client``.
"""

from __future__ import annotations

from app.core.features.arcgis_client import (
    AddLayerResult,
    ArcGISClient,
    DefaultHttpClient,
    HttpClientProtocol,
)

__all__ = [
    "AddLayerResult",
    "ArcGISClient",
    "DefaultHttpClient",
    "HttpClientProtocol",
]
