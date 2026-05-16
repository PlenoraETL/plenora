"""Neutral ArcGIS service discovery, folder, and portal item helpers."""

from __future__ import annotations

from typing import Callable, Optional, Tuple

from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.shared.connections.arcgis_provider import ArcGISDiscoveryProviderProtocol
from app.shared.contracts.payloads import JsonRows
from app.utils.logger import get_logger

logger = get_logger(__name__)

FolderFilter = Callable[[dict], bool]


def default_user_folder_filter(folder: dict) -> bool:
    """Return whether a raw ArcGIS folder represents a real user folder."""
    return bool(str(folder.get("id") or "").strip())


def list_user_folders(
    client: ArcGISDiscoveryProviderProtocol,
    conn: ArcGISConnectionInfo,
    *,
    folder_filter: FolderFilter = default_user_folder_filter,
) -> JsonRows:
    """List user folders through the provider with optional caller policy."""
    folders = client.list_folders(conn) or []
    return [folder for folder in folders if folder_filter(folder)]


def ensure_folder(
    client: ArcGISDiscoveryProviderProtocol,
    conn: ArcGISConnectionInfo,
    folder_name: str,
) -> Optional[str]:
    """Ensure a content folder and preserve root-folder compatibility."""
    if not folder_name:
        return ""
    return client.ensure_folder(conn, folder_name)


def is_service_name_available(
    client: ArcGISDiscoveryProviderProtocol,
    conn: ArcGISConnectionInfo,
    service_name: str,
) -> Tuple[bool, Optional[str]]:
    """Check whether an ArcGIS Feature Service name is available."""
    return client.is_service_name_available(conn, service_name)


def enumerate_user_items(
    client: ArcGISDiscoveryProviderProtocol,
    conn: ArcGISConnectionInfo,
) -> JsonRows:
    """List raw content items owned by the connected user."""
    return client.list_user_items(conn) or []


def search_user_feature_service_items(
    client: ArcGISDiscoveryProviderProtocol,
    conn: ArcGISConnectionInfo,
    service_name: str,
) -> JsonRows:
    """Find Feature Service items matching a title, service name, or URL."""
    all_items = enumerate_user_items(client, conn)
    feature_services = [
        item for item in all_items if item.get("type") == "Feature Service"
    ]

    logger.info(
        "search_user_feature_service_items: enumerated %s items, %s Feature "
        "Services for user %r. Looking for %r",
        len(all_items),
        len(feature_services),
        conn.username,
        service_name,
    )

    if not feature_services:
        return []

    matching = [item for item in feature_services if item.get("title") == service_name]
    if matching:
        logger.info(
            "search_user_feature_service_items: %s exact title matches",
            len(matching),
        )
        return matching

    service_name_lower = service_name.lower()
    matching = [
        item
        for item in feature_services
        if (item.get("title") or "").lower() == service_name_lower
    ]
    if matching:
        logger.info(
            "search_user_feature_service_items: %s case-insensitive title matches",
            len(matching),
        )
        return matching

    matching = [
        item
        for item in feature_services
        if (item.get("name") or "") == service_name
        or (item.get("name") or "").lower() == service_name_lower
    ]
    if matching:
        logger.info(
            "search_user_feature_service_items: %s service-name matches",
            len(matching),
        )
        return matching

    matching = [
        item
        for item in feature_services
        if f"/services/{service_name}/" in (item.get("url") or "")
    ]
    if matching:
        logger.info(
            "search_user_feature_service_items: %s URL-pattern matches",
            len(matching),
        )
        return matching

    titles_preview = [
        f"{item.get('title')} (name={item.get('name')})"
        for item in feature_services[:10]
    ]
    logger.warning(
        "search_user_feature_service_items: no match for %r. First user FS: %s",
        service_name,
        titles_preview,
    )
    return []


def find_existing_feature_service(
    client: ArcGISDiscoveryProviderProtocol,
    conn: ArcGISConnectionInfo,
    service_name: str,
) -> tuple:
    """Return ``(service_url, item_id, owner_folder)`` for a matching service."""
    matching = search_user_feature_service_items(client, conn, service_name)
    if not matching:
        return None, None, None
    item = matching[0]
    return item.get("url"), item.get("id"), item.get("ownerFolder")


__all__ = [
    "default_user_folder_filter",
    "ensure_folder",
    "enumerate_user_items",
    "find_existing_feature_service",
    "is_service_name_available",
    "list_user_folders",
    "search_user_feature_service_items",
]
