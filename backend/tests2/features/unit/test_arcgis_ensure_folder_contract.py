"""Contract tests for generic ArcGIS user-folder ensure boundary."""

from __future__ import annotations

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


class _FakeHttpClient:
    def __init__(self, get_payloads: list[dict], post_payloads: list[dict]) -> None:
        self.get_payloads = get_payloads
        self.post_payloads = post_payloads
        self.get_calls: list[dict] = []
        self.post_calls: list[dict] = []

    def get(
        self,
        url: str,
        params: dict = None,
        timeout: int = 30,
        headers: dict = None,
    ) -> _FakeResponse:
        self.get_calls.append({
            "url": url,
            "params": params or {},
            "timeout": timeout,
            "headers": headers or {},
        })
        return _FakeResponse(self.get_payloads.pop(0))

    def post(
        self,
        url: str,
        data: dict = None,
        timeout: int = 30,
        headers: dict = None,
    ) -> _FakeResponse:
        self.post_calls.append({
            "url": url,
            "data": data or {},
            "timeout": timeout,
            "headers": headers or {},
        })
        return _FakeResponse(self.post_payloads.pop(0))


def _conn() -> ArcGISConnectionInfo:
    return ArcGISConnectionInfo(
        server_url="https://org.maps.arcgis.com",
        server_type="arcgis_online",
        auth_type="public",
        username="publisher",
    )


def test_ensure_folder_returns_existing_folder_id_without_create() -> None:
    http = _FakeHttpClient(
        get_payloads=[{"folders": [{"id": "folder-1", "title": "IMDF Publish"}]}],
        post_payloads=[],
    )
    client = ArcGISClient(http_client=http)

    folder_id = client.ensure_folder(_conn(), "IMDF Publish")

    assert folder_id == "folder-1"
    assert http.get_calls == [
        {
            "url": "https://org.maps.arcgis.com/sharing/rest/content/users/publisher",
            "params": {"f": "json"},
            "timeout": 30,
            "headers": {"Referer": "https://org.maps.arcgis.com"},
        }
    ]
    assert http.post_calls == []


def test_ensure_folder_preserves_blank_root_folder_behavior() -> None:
    http = _FakeHttpClient(get_payloads=[], post_payloads=[])
    client = ArcGISClient(http_client=http)

    assert client.ensure_folder(_conn(), "") == ""
    assert http.get_calls == []
    assert http.post_calls == []


def test_ensure_folder_creates_missing_folder_and_returns_created_id() -> None:
    http = _FakeHttpClient(
        get_payloads=[{"folders": [{"id": "other", "title": "Other"}]}],
        post_payloads=[{"success": True, "folder": {"id": "folder-new"}}],
    )
    client = ArcGISClient(http_client=http)

    folder_id = client.ensure_folder(_conn(), "IMDF Publish")

    assert folder_id == "folder-new"
    assert http.post_calls == [
        {
            "url": (
                "https://org.maps.arcgis.com/sharing/rest/content/users/"
                "publisher/createFolder"
            ),
            "data": {"title": "IMDF Publish", "f": "json"},
            "timeout": 30,
            "headers": {"Referer": "https://org.maps.arcgis.com"},
        }
    ]


def test_ensure_folder_raises_for_error_shaped_create_response() -> None:
    http = _FakeHttpClient(
        get_payloads=[{"folders": []}],
        post_payloads=[{"error": {"code": 400, "message": "folder exists elsewhere"}}],
    )
    client = ArcGISClient(http_client=http)

    with pytest.raises(RuntimeError, match="createFolder fallito"):
        client.ensure_folder(_conn(), "IMDF Publish")
