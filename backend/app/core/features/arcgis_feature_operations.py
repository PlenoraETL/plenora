"""Provider orchestration for ArcGIS feature read/discovery operations.

This class keeps ArcGIS provider read/discovery calls out of the
route-facing connection/CRUD service while preserving the existing
FeaturesService public surface.
"""

from __future__ import annotations

from typing import List, Tuple

from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.shared.connections.arcgis_provider import ArcGISProviderProtocol
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ArcGISFeatureOperations:
    """Coordinates provider read/discovery calls for feature data."""

    def __init__(self, client: ArcGISProviderProtocol) -> None:
        self._client = client

    def test_connection(self, conn_info: ArcGISConnectionInfo) -> dict:
        return self._client.test_connection(conn_info)

    def list_services(self, conn_info: ArcGISConnectionInfo) -> List[dict]:
        return self._client.list_services(conn_info)

    def list_folders(self, conn_info: ArcGISConnectionInfo) -> List[dict]:
        return self._client.list_folders(conn_info)

    def search_services(
        self,
        conn_info: ArcGISConnectionInfo,
        query: str = "",
        scope: str = "user",
    ) -> List[dict]:
        if self._client.is_arcgis_online(conn_info):
            return self._client.search_services_online(conn_info, query=query, scope=scope)
        return self._client.list_services(conn_info)

    def list_layers(
        self,
        conn_info: ArcGISConnectionInfo,
        service_url: str,
    ) -> List[dict]:
        return self._client.list_layers(conn_info, service_url)

    def get_layer_schema(
        self,
        conn_info: ArcGISConnectionInfo,
        layer_url: str,
    ) -> dict:
        return self._client.get_layer_schema(conn_info, layer_url)

    def query_features(
        self,
        conn_info: ArcGISConnectionInfo,
        layer_url: str,
        where: str = "1=1",
        out_fields: str = "*",
        return_geometry: bool = True,
        max_features: int = None,
        out_sr: int = None,
    ) -> Tuple[List[tuple], List[str]]:
        return self._client.query_features(
            conn_info,
            layer_url,
            where=where,
            out_fields=out_fields,
            return_geometry=return_geometry,
            max_features=max_features,
            out_sr=out_sr,
        )

    def get_layer_crs(
        self,
        conn_info: ArcGISConnectionInfo,
        layer_url: str,
        out_sr: int = None,
    ) -> str:
        if out_sr:
            return f"EPSG:{out_sr}"

        try:
            schema = self.get_layer_schema(conn_info, layer_url)
            spatial_ref = schema.get("spatial_reference", {})
            wkid = spatial_ref.get("latestWkid") or spatial_ref.get("wkid")
            if wkid:
                logger.info(f"CRS layer: EPSG:{wkid}")
                return f"EPSG:{wkid}"
        except Exception as e:
            logger.warning(f"Impossibile determinare CRS del layer: {e}")

        return "EPSG:4326"
