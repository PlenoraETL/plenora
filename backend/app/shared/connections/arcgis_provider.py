"""Canonical ArcGIS Feature Service provider protocols.

The protocols in this module describe the lightweight provider boundary for
ArcGIS/ESRI Feature Service access.  They are intentionally client-shaped:
callers pass an ``ArcGISConnectionInfo`` plus service/layer URLs and the
provider performs discovery, reads, writes, and service administration.
Higher-level services may keep connection-id facades above this boundary.
"""

from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Protocol, Sequence, Tuple, runtime_checkable

from app.shared.connections.arcgis_connection import ArcGISConnectionInfo

FeaturePayload = Mapping[str, object]
ProviderResult = Mapping[str, object]
LayerRows = Tuple[List[tuple], List[str]]
OidLookupResult = Tuple[Dict[str, int], List[str]]


@runtime_checkable
class ArcGISDiscoveryProviderProtocol(Protocol):
    """Feature Service catalog discovery capability."""

    def test_connection(self, conn: ArcGISConnectionInfo) -> dict:
        """Validate authentication and basic Feature Service catalog access."""
        ...

    def list_services(
        self,
        conn: ArcGISConnectionInfo,
        folder: str = "",
        _depth: int = 0,
        _max_depth: int = 5,
    ) -> List[dict]:
        """List Feature Services available to the connection."""
        ...

    def is_arcgis_online(self, conn: ArcGISConnectionInfo) -> bool:
        """Return whether the connection targets ArcGIS Online."""
        ...

    def list_folders(self, conn: ArcGISConnectionInfo) -> List[dict]:
        """List folders available to the connection."""
        ...

    def ensure_folder(
        self,
        conn: ArcGISConnectionInfo,
        folder_name: str,
    ) -> Optional[str]:
        """Return an existing user folder id, or create the folder."""
        ...

    def list_user_items(self, conn: ArcGISConnectionInfo) -> List[dict]:
        """List raw content items owned by the connected user."""
        ...

    def search_items(
        self,
        conn: ArcGISConnectionInfo,
        query: str,
    ) -> List[dict]:
        """Search raw portal items with provider-managed pagination."""
        ...

    def is_service_name_available(
        self,
        conn: ArcGISConnectionInfo,
        service_name: str,
    ) -> Tuple[bool, Optional[str]]:
        """Check whether a Feature Service name is available on the portal."""
        ...

    def list_layers(
        self,
        conn: ArcGISConnectionInfo,
        service_url: str,
    ) -> List[dict]:
        """List layers/tables of a Feature Service URL."""
        ...

    def search_services_online(
        self,
        conn: ArcGISConnectionInfo,
        query: str = "",
        scope: str = "user",
    ) -> List[dict]:
        """Search ArcGIS Online Feature Service items in a provider-owned way."""
        ...


@runtime_checkable
class ArcGISReadProviderProtocol(Protocol):
    """Feature Service schema and query capability."""

    def count_features_where(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        where: str,
        *,
        timeout: float = 60.0,
    ) -> int:
        """Count features matching a WHERE clause via POST /query."""
        ...

    def get_layer_schema(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
    ) -> dict:
        """Return field schema (fields, spatial reference) for a single layer."""
        ...

    def query_features(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        where: str = "1=1",
        out_fields: str = "*",
        return_geometry: bool = True,
        geometry_filter: Optional[dict] = None,
        result_offset: int = 0,
        result_record_count: Optional[int] = None,
        order_by: Optional[str] = None,
        out_sr: Optional[int] = None,
        max_features: Optional[int] = None,
    ) -> LayerRows:
        """Query/read layer rows in the tuple shape consumed by Estrai."""
        ...

    def query_oid_by_key(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        match_col: str,
        values: Sequence[object],
    ) -> OidLookupResult:
        """Resolve OBJECTID values for business-key values on a layer."""
        ...


@runtime_checkable
class ArcGISWriteProviderProtocol(Protocol):
    """Feature Service feature mutation capability."""

    def dataframe_to_esri_features(
        self,
        df: object,
        geometry_column: str = "geometry",
        srid: int = 4326,
        operation: str = "add",
    ) -> List[dict]:
        """Convert tabular rows to ESRI JSON feature payloads."""
        ...

    def add_features(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        features: Sequence[FeaturePayload],
        batch_size: Optional[int] = None,
    ) -> ProviderResult:
        """Add ESRI JSON feature payloads to a layer."""
        ...

    def update_features(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        features: Sequence[FeaturePayload],
        batch_size: Optional[int] = None,
    ) -> ProviderResult:
        """Update ESRI JSON feature payloads in a layer."""
        ...

    def delete_features(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        object_ids: Optional[List[int]] = None,
        where: Optional[str] = None,
    ) -> ProviderResult:
        """Delete layer features by object ids or WHERE clause."""
        ...


@runtime_checkable
class ArcGISServiceAdminProviderProtocol(Protocol):
    """Feature Service creation and layer administration capability."""

    def create_service(
        self,
        conn: ArcGISConnectionInfo,
        service_name: str,
        layer_name: str,
        df: object,
        geometry_column: str = "geometry",
        srid: int = 4326,
        folder_id: str = "",
    ) -> ProviderResult:
        """Create a Feature Service and initial layer from tabular data."""
        ...

    def add_layer_to_service(
        self,
        conn: ArcGISConnectionInfo,
        service_url: str,
        gdf: object,
        layer_name: str,
        layer_id: int,
        srid: int = 4326,
    ) -> object:
        """Create an additional layer in an existing Feature Service."""
        ...

    def update_renderer(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        drawing_info: dict,
    ) -> ProviderResult:
        """Apply renderer/drawingInfo to a layer when the provider supports it."""
        ...

    def delete_item_permanently(
        self,
        conn: ArcGISConnectionInfo,
        item_id: str,
    ) -> ProviderResult:
        """Delete a portal item permanently, with Enterprise fallback support."""
        ...

    def delete_feature_service_items(
        self,
        conn: ArcGISConnectionInfo,
        items: Sequence[Mapping[str, object]],
        service_name: str = "",
    ) -> Tuple[int, int]:
        """Delete already-discovered Feature Service items and return counts."""
        ...

    def cleanup_zombie_feature_service(
        self,
        conn: ArcGISConnectionInfo,
        service_name: str,
    ) -> Tuple[bool, str]:
        """Delete a hosted service whose portal item is only discoverable via admin."""
        ...

    def update_layer_renderer(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        drawing_info: Mapping[str, object],
    ) -> ProviderResult:
        """Apply renderer/drawingInfo with layer-level updateDefinition."""
        ...


@runtime_checkable
class ArcGISProviderProtocol(
    ArcGISDiscoveryProviderProtocol,
    ArcGISReadProviderProtocol,
    ArcGISWriteProviderProtocol,
    ArcGISServiceAdminProviderProtocol,
    Protocol,
):
    """Canonical bidirectional ArcGIS Feature Service provider protocol.

    Existing callers may depend on narrower capability protocols when they only
    need discovery, read, write, or service-administration methods.
    """
    ...


__all__ = [
    "ArcGISDiscoveryProviderProtocol",
    "ArcGISProviderProtocol",
    "ArcGISReadProviderProtocol",
    "ArcGISServiceAdminProviderProtocol",
    "ArcGISWriteProviderProtocol",
    "FeaturePayload",
    "LayerRows",
    "OidLookupResult",
    "ProviderResult",
]
