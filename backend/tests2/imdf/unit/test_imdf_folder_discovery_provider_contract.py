"""IMDF folder discovery contract for the ArcGIS provider boundary."""

from __future__ import annotations

import pytest

from app.core.imdf.arcgis.service_discovery import (
    _ensure_folder,
    _enumerate_all_user_items,
    _list_user_folders,
)
from app.shared.connections.arcgis_connection import ArcGISConnectionInfo


pytestmark = [pytest.mark.unit, pytest.mark.contract]


class FakeFolderProvider:
    def __init__(self, folders: list[dict]) -> None:
        self.folders = folders
        self.calls: list[ArcGISConnectionInfo] = []

    def list_folders(self, conn: ArcGISConnectionInfo) -> list[dict]:
        self.calls.append(conn)
        return self.folders

    def ensure_folder(
        self,
        conn: ArcGISConnectionInfo,
        folder_name: str,
    ) -> str:
        self.calls.append(conn)
        return f"ensured:{folder_name}"


class FakeItemProvider:
    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.calls: list[ArcGISConnectionInfo] = []

    def list_user_items(self, conn: ArcGISConnectionInfo) -> list[dict]:
        self.calls.append(conn)
        return self.items


def test_imdf_user_folder_discovery_delegates_to_provider_list_folders() -> None:
    conn = ArcGISConnectionInfo(
        server_url="https://example.maps.arcgis.com",
        server_type="arcgis_online",
        auth_type="token",
        username="tester",
    )
    provider = FakeFolderProvider(
        [
            {"id": "", "title": "(root)"},
            {"id": "system", "title": "System"},
            {"id": "utilities", "title": "Utilities"},
            {"id": "folder-1", "title": "IMDF Publish"},
            {"id": "folder-2", "title": "Facilities", "created": 123},
        ]
    )

    folders = _list_user_folders(provider, conn)

    assert provider.calls == [conn]
    assert folders == [
        {"id": "folder-1", "title": "IMDF Publish"},
        {"id": "folder-2", "title": "Facilities", "created": 123},
    ]


def test_imdf_ensure_folder_delegates_to_provider_boundary() -> None:
    conn = ArcGISConnectionInfo(
        server_url="https://example.maps.arcgis.com",
        server_type="arcgis_online",
        auth_type="token",
        username="tester",
    )
    provider = FakeFolderProvider([])

    folder_id = _ensure_folder(provider, conn, "IMDF Publish")

    assert folder_id == "ensured:IMDF Publish"
    assert provider.calls == [conn]


def test_imdf_ensure_folder_preserves_root_folder_behavior() -> None:
    conn = ArcGISConnectionInfo(
        server_url="https://example.maps.arcgis.com",
        server_type="arcgis_online",
        auth_type="token",
        username="tester",
    )
    provider = FakeFolderProvider([])

    assert _ensure_folder(provider, conn, "") == ""
    assert provider.calls == []


def test_imdf_item_enumeration_delegates_to_provider_list_user_items() -> None:
    conn = ArcGISConnectionInfo(
        server_url="https://example.maps.arcgis.com",
        server_type="arcgis_online",
        auth_type="token",
        username="tester",
    )
    provider = FakeItemProvider(
        [
            {"id": "root-1", "title": "Root Item"},
            {"id": "folder-1", "title": "Folder Item", "ownerFolder": "folder-a"},
        ]
    )

    items = _enumerate_all_user_items(provider, conn)

    assert provider.calls == [conn]
    assert items == [
        {"id": "root-1", "title": "Root Item"},
        {"id": "folder-1", "title": "Folder Item", "ownerFolder": "folder-a"},
    ]
