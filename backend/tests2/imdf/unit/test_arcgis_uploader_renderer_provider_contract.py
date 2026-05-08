"""IMDF renderer publishing delegates to the ArcGIS provider boundary."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.imdf import arcgis_uploader


pytestmark = [pytest.mark.unit, pytest.mark.contract]


class _Rows:
    def __len__(self) -> int:
        return 1


def test_upload_imdf_layers_uses_provider_layer_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = SimpleNamespace(server_url="https://arc.test/portal", username="u")
    client = MagicMock(name="ArcGISProvider")
    client.create_service.return_value = {
        "service_url": "https://arc.test/server/rest/services/MyFs/FeatureServer",
        "item_id": "item-1",
        "feature_count": 1,
        "errors": [],
    }
    client.update_layer_renderer.return_value = {
        "success": True,
        "message": "Renderer aggiornato",
    }

    monkeypatch.setattr(
        "app.core.connections.arcgis_connection_resolver.resolve_arcgis_connection",
        lambda connection_id: (conn, client),
    )
    monkeypatch.setattr(
        "app.core.imdf.arcgis.service_lifecycle._delete_existing_service",
        lambda client, conn, fs_name, folder_id: (0, 0),
    )
    monkeypatch.setattr(
        "app.core.imdf.arcgis.service_discovery._is_service_name_available",
        lambda client, conn, fs_name: (True, None),
    )

    result = arcgis_uploader.upload_imdf_layers(
        connection_id="conn-1",
        layers={"Localita": _Rows()},
        fs_name="MyFs",
        apply_renderers=True,
        renderers_data={"Localita": {"renderer": {"type": "simple"}}},
    )

    assert result["renderers_applied"] == 1
    client.update_layer_renderer.assert_called_once_with(
        conn=conn,
        layer_url="https://arc.test/server/rest/services/MyFs/FeatureServer/0",
        drawing_info={"renderer": {"type": "simple"}},
    )
    client._http.post.assert_not_called()
