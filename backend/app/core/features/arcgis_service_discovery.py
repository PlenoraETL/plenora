"""ArcGIS service discovery, folder, and portal item listing helpers."""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

import requests

from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.shared.connections.arcgis_provider_results import (
    FolderEnsureResult,
    ItemListingResult,
    ItemSearchResult,
    ServiceNameAvailabilityResult,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

FolderFilter = Callable[[dict], bool]


def default_user_folder_filter(folder: dict) -> bool:
    """Return whether a raw ArcGIS folder represents a real user folder."""
    return bool(str(folder.get("id") or "").strip())


def list_user_folders(
    client,
    conn: ArcGISConnectionInfo,
    *,
    folder_filter: FolderFilter = default_user_folder_filter,
) -> List[dict]:
    """List user folders through the provider with optional caller policy."""
    folders = client.list_folders(conn) or []
    return [folder for folder in folders if folder_filter(folder)]


def ensure_folder(client, conn: ArcGISConnectionInfo, folder_name: str) -> Optional[str]:
    """Ensure a content folder and preserve root-folder compatibility."""
    if not folder_name:
        return ""
    return client.ensure_folder(conn, folder_name)


def is_service_name_available(
    client,
    conn: ArcGISConnectionInfo,
    service_name: str,
) -> Tuple[bool, Optional[str]]:
    """Check whether an ArcGIS Feature Service name is available."""
    return client.is_service_name_available(conn, service_name)


def enumerate_user_items(client, conn: ArcGISConnectionInfo) -> List[dict]:
    """List raw content items owned by the connected user."""
    return client.list_user_items(conn) or []


def search_user_feature_service_items(
    client,
    conn: ArcGISConnectionInfo,
    service_name: str,
) -> List[dict]:
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
    client,
    conn: ArcGISConnectionInfo,
    service_name: str,
) -> tuple:
    """Return ``(service_url, item_id, owner_folder)`` for a matching service."""
    matching = search_user_feature_service_items(client, conn, service_name)
    if not matching:
        return None, None, None
    item = matching[0]
    return item.get("url"), item.get("id"), item.get("ownerFolder")


class ArcGISServiceDiscoveryMixin:
    """Service discovery provider methods mixed into ``ArcGISClient``."""

    def list_services(
        self,
        conn: ArcGISConnectionInfo,
        folder: str = "",
        _depth: int = 0,
        _max_depth: int = 5,
    ) -> List[dict]:
        """List available Feature Services."""
        if self._is_arcgis_online(conn):
            return self._list_services_online(conn, folder=folder)
        return self._list_services_server(
            conn,
            folder=folder,
            _depth=_depth,
            _max_depth=_max_depth,
        )

    def _is_arcgis_online(self, conn: ArcGISConnectionInfo) -> bool:
        """Return whether the connection targets ArcGIS Online."""
        url = conn.server_url.lower()
        return "arcgis.com" in url or conn.server_type == "arcgis_online"

    def is_arcgis_online(self, conn: ArcGISConnectionInfo) -> bool:
        """Public provider capability for ArcGIS Online detection."""
        return self._is_arcgis_online(conn)

    def _list_services_online(
        self,
        conn: ArcGISConnectionInfo,
        folder: str = "",
    ) -> List[dict]:
        """List user Feature Services through the ArcGIS Online Content API."""
        _ = self.authenticate(conn)
        username = conn.username

        if not username:
            logger.info("Nessun username - uso Search API per l'organizzazione")
            return self.search_services_online(conn, query="", scope="org")

        from urllib.parse import urlparse

        parsed = urlparse(conn.server_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}/sharing/rest"

        try:
            if folder:
                content_url = f"{base_url}/content/users/{username}/{folder}"
            else:
                content_url = f"{base_url}/content/users/{username}"

            response = self._request_with_token(
                "GET",
                content_url,
                conn,
                params={"f": "json", "num": 100},
                timeout=self.DEFAULT_TIMEOUT,
            )
            data = response.json()

            if "error" in data:
                error_msg = data["error"].get("message", "Errore Content API")
                raise ConnectionError(f"Errore ArcGIS Online: {error_msg}")

            services = []
            for item in data.get("items", []):
                if item.get("type") != "Feature Service":
                    continue
                item_url = item.get("url", "")
                if not item_url:
                    continue

                logger.info(f"Content API item: {item.get('title')} -> {item_url}")
                services.append({
                    "name": item.get("title", item.get("name", "Senza nome")),
                    "full_name": item.get("name", item.get("title", "")),
                    "url": item_url,
                    "folder": folder or item.get("ownerFolder", ""),
                    "service_type": "FeatureServer",
                    "item_id": item.get("id", ""),
                    "owner": item.get("owner", username),
                    "num_views": item.get("numViews", 0),
                    "modified": item.get("modified", 0),
                })

            if not folder:
                for sub_folder in data.get("folders", []):
                    folder_id = sub_folder.get("id", "")
                    folder_title = sub_folder.get("title", folder_id)
                    if not folder_id:
                        continue
                    sub_services = self._list_services_online(conn, folder=folder_id)
                    for svc in sub_services:
                        svc["folder"] = folder_title
                    services.extend(sub_services)

            logger.info(
                f"ArcGIS Online: trovati {len(services)} Feature Service per {username}"
            )
            return services
        except requests.RequestException as e:
            raise ConnectionError(f"Errore connessione ArcGIS Online: {e}")

    def _list_user_content_page(
        self,
        conn: ArcGISConnectionInfo,
        base_url: str,
        username: str,
        folder_id: str,
        start: int,
        num: int,
    ) -> dict:
        content_url = (
            f"{base_url}/content/users/{username}/{folder_id}"
            if folder_id
            else f"{base_url}/content/users/{username}"
        )
        response = self._request_with_token(
            "GET",
            content_url,
            conn,
            params={"f": "json", "num": num, "start": start},
            timeout=self.DEFAULT_TIMEOUT,
            extra_headers={"Referer": conn.server_url},
        )
        data = response.json()
        if "error" in data:
            message = data["error"].get("message", "Errore Content API")
            raise ConnectionError(f"Errore ArcGIS content items: {message}")
        return data

    def _list_user_items_in_folder(
        self,
        conn: ArcGISConnectionInfo,
        base_url: str,
        username: str,
        folder_id: str,
        *,
        num: int,
    ) -> Tuple[List[dict], List[dict]]:
        items: List[dict] = []
        folders: List[dict] = []
        start = 1
        first_page = True

        while True:
            data = self._list_user_content_page(
                conn,
                base_url,
                username,
                folder_id,
                start,
                num,
            )
            if first_page and not folder_id:
                folders = list(data.get("folders", []) or [])
                first_page = False

            page_items = data.get("items", []) or []
            items.extend(page_items)

            next_start = data.get("nextStart", -1)
            if next_start == -1 or next_start <= start or not page_items:
                break
            start = next_start

        return items, folders

    def _list_user_items_result(self, conn: ArcGISConnectionInfo) -> ItemListingResult:
        """List raw ArcGIS content items as a typed provider result."""
        username = conn.username
        if not username:
            return ItemListingResult(items=())

        base_url = self._portal_sharing_base_url(conn)
        root_items, folders = self._list_user_items_in_folder(
            conn,
            base_url,
            username,
            "",
            num=100,
        )
        all_items = list(root_items)

        for folder in folders:
            folder_id = folder.get("id")
            if not folder_id:
                continue
            folder_items, _ = self._list_user_items_in_folder(
                conn,
                base_url,
                username,
                str(folder_id),
                num=100,
            )
            all_items.extend(folder_items)

        return ItemListingResult(items=tuple(all_items))

    def list_user_items(self, conn: ArcGISConnectionInfo) -> List[dict]:
        """List raw ArcGIS content items for the connected user."""
        return self._list_user_items_result(conn).to_compat_list()

    def list_folders(self, conn: ArcGISConnectionInfo) -> List[dict]:
        """List user folders, including root as ``{"id": "", "title": "(root)"}``."""
        if self._is_arcgis_online(conn):
            return self._list_folders_online(conn)
        return self._list_folders_server(conn)

    def _list_folders_online(self, conn: ArcGISConnectionInfo) -> List[dict]:
        """List user folders through the ArcGIS Online Content API."""
        _ = self.authenticate(conn)
        username = conn.username
        if not username:
            return [{"id": "", "title": "(root)"}]

        from urllib.parse import urlparse

        parsed = urlparse(conn.server_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}/sharing/rest"
        content_url = f"{base_url}/content/users/{username}"

        try:
            response = self._request_with_token(
                "GET",
                content_url,
                conn,
                params={"f": "json"},
                timeout=self.DEFAULT_TIMEOUT,
            )
            data = response.json()
            if "error" in data:
                logger.warning(f"Errore lista folders: {data['error']}")
                return [{"id": "", "title": "(root)"}]

            folders = [{"id": "", "title": "(root)"}]
            for folder in data.get("folders", []):
                folders.append({
                    "id": folder.get("id", ""),
                    "title": folder.get("title", folder.get("id", "")),
                })
            return folders
        except requests.RequestException as e:
            logger.warning(f"Errore lista folders: {e}")
            return [{"id": "", "title": "(root)"}]

    def _list_folders_server(self, conn: ArcGISConnectionInfo) -> List[dict]:
        """List folders from the ArcGIS Server/Enterprise REST catalog."""
        catalog_url = f"{conn.server_url.rstrip('/')}/rest/services"

        try:
            response = self._request_with_token(
                "GET",
                catalog_url,
                conn,
                params={"f": "json"},
                timeout=self.DEFAULT_TIMEOUT,
            )
            data = response.json()

            folders = [{"id": "", "title": "(root)"}]
            for folder in data.get("folders", []):
                folders.append({"id": folder, "title": folder})
            return folders
        except requests.RequestException as e:
            logger.warning(f"Errore lista folders server: {e}")
            return [{"id": "", "title": "(root)"}]

    def _ensure_folder_result(
        self,
        conn: ArcGISConnectionInfo,
        folder_name: str,
    ) -> FolderEnsureResult:
        """Find or create a user content folder as a typed provider result."""
        normalized_name = folder_name.strip()
        if not normalized_name:
            return FolderEnsureResult(folder_id="", title="", created=False)
        if not conn.username:
            raise ValueError("ArcGIS username richiesto per ensure_folder")

        from urllib.parse import urlparse

        parsed = urlparse(conn.server_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}/sharing/rest"
        referer = {"Referer": conn.server_url}

        list_response = self._request_with_token(
            "GET",
            f"{base_url}/content/users/{conn.username}",
            conn,
            params={"f": "json"},
            timeout=self.DEFAULT_TIMEOUT,
            extra_headers=referer,
        )
        list_data = list_response.json()
        for folder in list_data.get("folders", []) or []:
            if folder.get("title") == normalized_name:
                return FolderEnsureResult(
                    folder_id=folder.get("id"),
                    title=normalized_name,
                    created=False,
                )

        create_response = self._request_with_token(
            "POST",
            f"{base_url}/content/users/{conn.username}/createFolder",
            conn,
            data={"title": normalized_name, "f": "json"},
            timeout=self.DEFAULT_TIMEOUT,
            extra_headers=referer,
        )
        create_data = create_response.json()
        if not create_data.get("success"):
            raise RuntimeError(f"createFolder fallito: {create_data}")
        return FolderEnsureResult(
            folder_id=(create_data.get("folder") or {}).get("id"),
            title=normalized_name,
            created=True,
        )

    def ensure_folder(
        self,
        conn: ArcGISConnectionInfo,
        folder_name: str,
    ) -> Optional[str]:
        """Find or create a user content folder and return its folder id."""
        return self._ensure_folder_result(conn, folder_name).to_compat_id()

    def _service_name_availability_result(
        self,
        conn: ArcGISConnectionInfo,
        service_name: str,
    ) -> ServiceNameAvailabilityResult:
        """Check whether a Feature Service name is available on the portal."""
        from urllib.parse import urlparse

        parsed = urlparse(conn.server_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}/sharing/rest"
        params = {
            "name": service_name,
            "type": "Feature Service",
            "f": "json",
        }

        try:
            response = self._request_with_token(
                "GET",
                f"{base_url}/portals/self/isServiceNameAvailable",
                conn,
                params=params,
                timeout=20,
                extra_headers={"Referer": conn.server_url},
            )
            data = response.json()
            if "error" in data:
                message = data["error"].get("message", "")
                return ServiceNameAvailabilityResult(
                    available=True,
                    error=f"check fallito: {message}",
                )
            return ServiceNameAvailabilityResult(
                available=bool(data.get("available", False)),
                error=None,
            )
        except Exception as e:
            logger.warning(f"is_service_name_available error: {e}")
            return ServiceNameAvailabilityResult(available=True, error=str(e))

    def is_service_name_available(
        self,
        conn: ArcGISConnectionInfo,
        service_name: str,
    ) -> Tuple[bool, Optional[str]]:
        """Check whether a Feature Service name is available on the portal."""
        return self._service_name_availability_result(
            conn,
            service_name,
        ).to_compat_tuple()

    def _search_items_result(
        self,
        conn: ArcGISConnectionInfo,
        query: str,
    ) -> ItemSearchResult:
        """Search raw portal items as a typed provider result."""
        base_url = self._portal_sharing_base_url(conn)
        all_results: List[dict] = []
        start = 1

        try:
            while True:
                params = {
                    "q": query,
                    "f": "json",
                    "num": 100,
                    "start": start,
                    "sortField": "modified",
                    "sortOrder": "desc",
                }
                response = self._request_with_token(
                    "GET",
                    f"{base_url}/search",
                    conn,
                    params=params,
                    timeout=self.DEFAULT_TIMEOUT,
                )
                data = response.json()

                if "error" in data:
                    error_msg = data["error"].get("message", "Errore ricerca")
                    raise ConnectionError(f"Errore ricerca ArcGIS Online: {error_msg}")

                results = data.get("results", []) or []
                all_results.extend(results)

                next_start = data.get("nextStart", -1)
                if next_start == -1 or next_start <= start or not results:
                    break
                start = next_start

            return ItemSearchResult(items=tuple(all_results))
        except requests.RequestException as e:
            raise ConnectionError(f"Errore ricerca ArcGIS Online: {e}")

    def search_items(
        self,
        conn: ArcGISConnectionInfo,
        query: str,
    ) -> List[dict]:
        """Search raw portal items with provider-managed pagination."""
        return self._search_items_result(conn, query).to_compat_list()

    def search_services_online(
        self,
        conn: ArcGISConnectionInfo,
        query: str = "",
        scope: str = "user",
    ) -> List[dict]:
        """Search Feature Services on ArcGIS Online."""
        username = conn.username
        search_parts = ['type:"Feature Service"']

        if scope == "user" and username:
            search_parts.append(f"owner:{username}")
        elif scope == "org":
            search_parts.append("orgid:self")

        if query:
            search_parts.append(query)

        search_query = " ".join(search_parts)

        results = []
        for item in self.search_items(conn, search_query):
            item_url = item.get("url", "")
            if not item_url:
                continue

            results.append({
                "name": item.get("title", item.get("name", "Senza nome")),
                "full_name": item.get("name", item.get("title", "")),
                "url": item_url,
                "folder": "",
                "service_type": "FeatureServer",
                "item_id": item.get("id", ""),
                "owner": item.get("owner", ""),
                "num_views": item.get("numViews", 0),
                "modified": item.get("modified", 0),
            })

        logger.info(f"Ricerca ArcGIS Online '{search_query}': {len(results)} risultati")
        return results

    def _list_services_server(
        self,
        conn: ArcGISConnectionInfo,
        folder: str = "",
        _depth: int = 0,
        _max_depth: int = 5,
    ) -> List[dict]:
        """List Feature Services from the ArcGIS Server/Enterprise catalog."""
        base = self._get_rest_services_url(conn)
        catalog_url = f"{base}/{folder}" if folder else base

        try:
            response = self._request_with_token(
                "GET",
                catalog_url,
                conn,
                params={"f": "json"},
                timeout=self.DEFAULT_TIMEOUT,
            )
            data = response.json()

            if "error" in data:
                raise ConnectionError(
                    f"Errore catalogo: {data['error'].get('message', '')}"
                )

            services = []
            for svc in data.get("services", []):
                if svc.get("type") != "FeatureServer":
                    continue
                svc_name = svc["name"]
                display_name = svc_name.split("/")[-1] if "/" in svc_name else svc_name
                services.append({
                    "name": display_name,
                    "full_name": svc_name,
                    "url": f"{base}/{svc_name}/FeatureServer",
                    "folder": folder,
                    "service_type": "FeatureServer",
                })

            if _depth < _max_depth:
                for sub_folder in data.get("folders", []):
                    folder_path = f"{folder}/{sub_folder}" if folder else sub_folder
                    services.extend(
                        self._list_services_server(
                            conn,
                            folder=folder_path,
                            _depth=_depth + 1,
                            _max_depth=_max_depth,
                        )
                    )

            return services
        except requests.RequestException as e:
            raise ConnectionError(f"Errore connessione al catalogo: {e}")

    def _get_rest_services_url(self, conn: ArcGISConnectionInfo) -> str:
        """Return the REST services catalog URL for an ArcGIS connection."""
        base = conn.server_url.rstrip("/")

        if base.endswith("/rest/services"):
            return base
        if base.endswith("/rest"):
            return f"{base}/services"
        if "/rest/services" in base:
            idx = base.index("/rest/services")
            return base[: idx + len("/rest/services")]
        return f"{base}/rest/services"
