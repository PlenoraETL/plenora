"""Contract tests for ArcGIS provider service/item lifecycle methods."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.features.arcgis_client import ArcGISClient
from app.shared.connections.arcgis_connection import ArcGISConnectionInfo


pytestmark = [pytest.mark.unit, pytest.mark.contract]


class _FakeResponse:
    status_code = 200
    headers: dict[str, str] = {}

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def conn() -> ArcGISConnectionInfo:
    return ArcGISConnectionInfo(
        server_url="https://portal.example.com/portal",
        server_type="arcgis_online",
        auth_type="token",
        username="owner",
        password="secret",
    )


def _client_with_http(http: MagicMock) -> ArcGISClient:
    client = ArcGISClient(http_client=http)
    client.authenticate = MagicMock(return_value="tok")  # type: ignore[method-assign]
    return client


def test_delete_item_permanently_posts_permanent_delete_without_body_token(
    conn: ArcGISConnectionInfo,
) -> None:
    http = MagicMock()
    http.post.return_value = _FakeResponse({"success": True})
    client = _client_with_http(http)

    result = client.delete_item_permanently(conn, "item-1")

    assert result["success"] is True
    http.post.assert_called_once()
    url = http.post.call_args.args[0]
    kwargs = http.post.call_args.kwargs
    assert url == "https://portal.example.com/sharing/rest/content/users/owner/items/item-1/delete"
    assert kwargs["data"] == {"f": "json", "permanentDelete": "true"}
    assert kwargs["headers"] == {
        "Referer": conn.server_url,
        "Authorization": "Bearer tok",
    }


def test_delete_item_permanently_falls_back_to_permanent_delete_endpoint(
    conn: ArcGISConnectionInfo,
) -> None:
    http = MagicMock()
    http.post.side_effect = [
        _FakeResponse({"success": False, "error": "permanent flag unsupported"}),
        _FakeResponse({"success": True}),
        _FakeResponse({"success": True}),
    ]
    client = _client_with_http(http)

    result = client.delete_item_permanently(conn, "item-2")

    assert result["success"] is True
    urls = [call.args[0] for call in http.post.call_args_list]
    assert urls == [
        "https://portal.example.com/sharing/rest/content/users/owner/items/item-2/delete",
        "https://portal.example.com/sharing/rest/content/users/owner/items/item-2/delete",
        "https://portal.example.com/sharing/rest/content/users/owner/items/item-2/permanentDelete",
    ]
    assert http.post.call_args_list[1].kwargs["data"] == {"f": "json"}
    assert http.post.call_args_list[2].kwargs["data"] == {"f": "json"}


def test_delete_feature_service_items_counts_only_items_with_ids(
    conn: ArcGISConnectionInfo,
) -> None:
    client = ArcGISClient(http_client=MagicMock())
    client.delete_item_permanently = MagicMock(  # type: ignore[method-assign]
        side_effect=[
            {"success": True},
            {"success": False, "message": "denied"},
        ]
    )

    result = client.delete_feature_service_items(
        conn,
        [
            {"id": "item-1", "ownerFolder": "root"},
            {"ownerFolder": "missing-id"},
            {"id": "item-2", "ownerFolder": None},
        ],
        "MyFS",
    )

    assert result == (2, 1)
    assert client.delete_item_permanently.call_count == 2


def test_cleanup_zombie_feature_service_uses_hosting_admin_describe_then_item_delete(
    conn: ArcGISConnectionInfo,
) -> None:
    http = MagicMock()
    http.get.side_effect = [
        _FakeResponse(
            {
                "helperServices": {
                    "hostingServers": [
                        {"admin": "https://services.example.com/org/arcgis"},
                    ]
                }
            }
        ),
        _FakeResponse({"serviceItemId": "zombie-item"}),
    ]
    http.post.return_value = _FakeResponse({"success": True})
    client = _client_with_http(http)

    cleaned, message = client.cleanup_zombie_feature_service(conn, "ZombieFS")

    assert cleaned is True
    assert message == "item zombie-item cancellato"
    get_urls = [call.args[0] for call in http.get.call_args_list]
    assert get_urls == [
        "https://portal.example.com/sharing/rest/portals/self",
        "https://services.example.com/org/arcgis/admin/services/ZombieFS.FeatureServer",
    ]
    assert http.post.call_args.args[0] == (
        "https://portal.example.com/sharing/rest/content/users/owner/items/zombie-item/delete"
    )
