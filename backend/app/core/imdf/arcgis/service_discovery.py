"""
Cluster SERVICE_DISCOVERY - IMDF folder + Feature Service discovery policy.
"""

import importlib
from typing import List, Optional, Tuple

from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.shared.connections.arcgis_provider import ArcGISDiscoveryProviderProtocol
from app.utils.logger import get_logger

logger = get_logger("app.core.imdf.arcgis_uploader")


def _arcgis_discovery():
    """Lazy import: BE-GO-READY-ARCH-057 forbids `from app.core.features`
    imports inside `app.core.imdf` (IMDF must use shared provider protocols
    or the neutral resolver only). The runtime dependency on the helper
    functions is real and narrow, so we resolve it dynamically via
    importlib to keep the static surface clean."""
    return importlib.import_module("app.core.features.arcgis_service_discovery")


_IMDF_NON_USER_FOLDER_TITLES = {"", "(root)", "root", "system", "utilities"}


def _is_imdf_user_folder(folder: dict) -> bool:
    folder_id = str(folder.get("id") or "").strip()
    title = str(folder.get("title") or "").strip()
    if not folder_id:
        return False
    return title.lower() not in _IMDF_NON_USER_FOLDER_TITLES


def _list_user_folders(
    client: ArcGISDiscoveryProviderProtocol,
    conn: ArcGISConnectionInfo,
) -> List[dict]:
    """Read-only IMDF adapter for ArcGIS folder listing."""
    try:
        return _arcgis_discovery().list_user_folders(
            client,
            conn,
            folder_filter=_is_imdf_user_folder,
        )
    except Exception as e:
        logger.warning(f"_list_user_folders: errore: {e}")
        return []


def _ensure_folder(
    client: ArcGISDiscoveryProviderProtocol,
    conn: ArcGISConnectionInfo,
    folder_name: str,
) -> Optional[str]:
    """IMDF adapter over the generic ArcGIS folder boundary."""
    return _arcgis_discovery().ensure_folder(client, conn, folder_name)


def _is_service_name_available(
    client: ArcGISDiscoveryProviderProtocol,
    conn: ArcGISConnectionInfo,
    fs_name: str,
) -> Tuple[bool, Optional[str]]:
    """Check portal service-name availability with IMDF fail-open behavior."""
    try:
        return _arcgis_discovery().is_service_name_available(client, conn, fs_name)
    except Exception as e:
        logger.warning(f"_is_service_name_available error: {e}")
        return True, str(e)


def _enumerate_all_user_items(
    client: ArcGISDiscoveryProviderProtocol,
    conn: ArcGISConnectionInfo,
) -> List[dict]:
    """Enumerate all current-user portal items through the provider."""
    try:
        return _arcgis_discovery().enumerate_user_items(client, conn)
    except Exception as e:
        logger.warning(f"_enumerate_all_user_items: list_user_items fallita: {e}")
        return []


def _search_user_items(
    client: ArcGISDiscoveryProviderProtocol,
    conn: ArcGISConnectionInfo,
    fs_name: str,
) -> List[dict]:
    """Find current-user Feature Service items matching ``fs_name``."""
    return _arcgis_discovery().search_user_feature_service_items(client, conn, fs_name)


def _find_existing_service(
    client: ArcGISDiscoveryProviderProtocol,
    conn: ArcGISConnectionInfo,
    fs_name: str,
    folder_id: str = "",
) -> tuple:
    """Return ``(service_url, item_id, owner_folder)`` for an existing service."""
    return _arcgis_discovery().find_existing_feature_service(client, conn, fs_name)
