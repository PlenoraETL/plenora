"""IMDF folder discovery contract for the ArcGIS provider boundary."""

from __future__ import annotations

import pytest

from app.core.imdf.arcgis.service_discovery import _list_user_folders
from app.shared.connections.arcgis_connection import ArcGISConnectionInfo


pytestmark = [pytest.mark.unit, pytest.mark.contract]


class FakeFolderProvider:
    def __init__(self, folders: list[dict]) -> None:
        self.folders = folders
        self.calls: list[ArcGISConnectionInfo] = []

    def list_folders(self, conn: ArcGISConnectionInfo) -> list[dict]:
        self.calls.append(conn)
        return self.folders


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
