"""Generic ArcGIS Feature Service lifecycle orchestration helpers."""

from __future__ import annotations

from typing import Tuple

from app.core.features.arcgis_service_discovery import search_user_feature_service_items
from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.utils.logger import get_logger

logger = get_logger(__name__)


def try_cleanup_zombie_service(
    client,
    conn: ArcGISConnectionInfo,
    service_name: str,
) -> Tuple[bool, str]:
    """Delete a hosted service whose portal item is only discoverable via admin."""
    return client.cleanup_zombie_feature_service(conn, service_name)


def delete_existing_feature_service(
    client,
    conn: ArcGISConnectionInfo,
    service_name: str,
) -> Tuple[int, int]:
    """Discover and delete matching Feature Service portal items."""
    matching = search_user_feature_service_items(client, conn, service_name)
    if not matching:
        return (0, 0)

    attempted, succeeded = client.delete_feature_service_items(
        conn,
        matching,
        service_name,
    )
    if attempted != succeeded:
        logger.warning(
            "delete_existing_feature_service(%r): partial delete %s/%s items; "
            "the service name may still be reserved",
            service_name,
            succeeded,
            attempted,
        )
    return attempted, succeeded


__all__ = ["delete_existing_feature_service", "try_cleanup_zombie_service"]
