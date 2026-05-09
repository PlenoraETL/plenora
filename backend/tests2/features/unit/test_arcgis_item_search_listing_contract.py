"""Contract tests for ArcGIS raw item listing/search provider methods."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.features.arcgis_client import ArcGISClient
from app.core.features.arcgis_service_discovery import (
    find_existing_feature_service,
    list_user_folders,
    search_user_feature_service_items,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract]


class _FakeResponse:
    status_code = 200
    headers: dict[str, str] = {}

    def __init__(self, body: dict) -> None:
        self._body = body

    def json(self) -> dict:
        return self._body


@pytest.fixture
def conn() -> SimpleNamespace:
    return SimpleNamespace(
        server_url="https://org.maps.arcgis.com",
        server_type="arcgis_online",
        username="publisher",
        client_id=None,
    )


@pytest.fixture
def client() -> ArcGISClient:
    fake_http = MagicMock(name="HttpClient")
    arcgis_client = ArcGISClient(http_client=fake_http)
    arcgis_client.authenticate = MagicMock(return_value="tok-123")
    return arcgis_client


def test_list_user_items_paginates_root_and_user_folders(
    client: ArcGISClient,
    conn: SimpleNamespace,
) -> None:
    client._http.get.side_effect = [
        _FakeResponse(
            {
                "items": [{"id": "root-1", "title": "Root One"}],
                "folders": [{"id": "folder-a", "title": "Folder A"}],
                "nextStart": 2,
            }
        ),
        _FakeResponse(
            {
                "items": [{"id": "root-2", "title": "Root Two"}],
                "nextStart": -1,
            }
        ),
        _FakeResponse(
            {
                "items": [{"id": "folder-1", "title": "Folder One"}],
                "nextStart": 2,
            }
        ),
        _FakeResponse(
            {
                "items": [{"id": "folder-2", "title": "Folder Two"}],
                "nextStart": -1,
            }
        ),
    ]

    items = client.list_user_items(conn)

    assert items == [
        {"id": "root-1", "title": "Root One"},
        {"id": "root-2", "title": "Root Two"},
        {"id": "folder-1", "title": "Folder One"},
        {"id": "folder-2", "title": "Folder Two"},
    ]
    urls = [call.args[0] for call in client._http.get.call_args_list]
    assert urls == [
        "https://org.maps.arcgis.com/sharing/rest/content/users/publisher",
        "https://org.maps.arcgis.com/sharing/rest/content/users/publisher",
        "https://org.maps.arcgis.com/sharing/rest/content/users/publisher/folder-a",
        "https://org.maps.arcgis.com/sharing/rest/content/users/publisher/folder-a",
    ]
    starts = [call.kwargs["params"]["start"] for call in client._http.get.call_args_list]
    assert starts == [1, 2, 1, 2]
    assert client._http.get.call_args_list[0].kwargs["headers"] == {
        "Authorization": "Bearer tok-123",
        "Referer": conn.server_url,
    }


def test_search_items_paginates_and_preserves_raw_result_shape(
    client: ArcGISClient,
    conn: SimpleNamespace,
) -> None:
    first = {
        "id": "item-1",
        "title": "IMDF Demo",
        "type": "Feature Service",
        "name": "imdf_demo",
        "url": "https://services.arcgis.com/abc/arcgis/rest/services/imdf_demo/FeatureServer",
        "ownerFolder": "folder-a",
        "numViews": 7,
    }
    second = {
        "id": "item-2",
        "title": "Layer File",
        "type": "Feature Collection",
        "custom": {"kept": True},
    }
    client._http.get.side_effect = [
        _FakeResponse({"results": [first], "nextStart": 2}),
        _FakeResponse({"results": [second], "nextStart": -1}),
    ]

    items = client.search_items(conn, 'type:"Feature Service" owner:publisher')

    assert items == [first, second]
    assert [call.args[0] for call in client._http.get.call_args_list] == [
        "https://org.maps.arcgis.com/sharing/rest/search",
        "https://org.maps.arcgis.com/sharing/rest/search",
    ]
    assert [call.kwargs["params"]["start"] for call in client._http.get.call_args_list] == [1, 2]
    assert client._http.get.call_args_list[0].kwargs["params"]["q"] == (
        'type:"Feature Service" owner:publisher'
    )


class _FakeDiscoveryProvider:
    def __init__(self) -> None:
        self.folders = [
            {"id": "", "title": "(root)"},
            {"id": "folder-a", "title": "Facilities"},
        ]
        self.items = [
            {
                "id": "fs-1",
                "title": "Indoor",
                "type": "Feature Service",
                "name": "indoor_service",
                "url": (
                    "https://services.arcgis.com/org/arcgis/rest/services/"
                    "Indoor/FeatureServer"
                ),
                "ownerFolder": "folder-a",
            },
            {"id": "doc-1", "title": "Indoor", "type": "PDF"},
        ]

    def list_folders(self, conn: SimpleNamespace) -> list[dict]:
        return self.folders

    def list_user_items(self, conn: SimpleNamespace) -> list[dict]:
        return self.items


def test_generic_service_discovery_helpers_filter_and_find_feature_services(
    conn: SimpleNamespace,
) -> None:
    provider = _FakeDiscoveryProvider()

    folders = list_user_folders(
        provider,
        conn,
        folder_filter=lambda folder: bool(folder.get("id")),
    )
    matches = search_user_feature_service_items(provider, conn, "indoor_service")
    existing = find_existing_feature_service(provider, conn, "Indoor")

    assert folders == [{"id": "folder-a", "title": "Facilities"}]
    assert matches == [provider.items[0]]
    assert existing == (
        provider.items[0]["url"],
        "fs-1",
        "folder-a",
    )
