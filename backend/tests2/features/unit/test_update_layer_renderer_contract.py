"""Contract tests for ArcGIS layer-level renderer updates."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.features.arcgis_client import ArcGISClient


pytestmark = [pytest.mark.unit, pytest.mark.contract]


class _Response:
    status_code = 200
    headers: dict[str, str] = {}

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


@pytest.fixture
def client() -> ArcGISClient:
    arcgis_client = ArcGISClient.__new__(ArcGISClient)
    arcgis_client._token_cache = {}
    arcgis_client._http = MagicMock()
    arcgis_client._http.post = MagicMock(return_value=_Response({"success": True}))
    arcgis_client.authenticate = MagicMock(return_value="tok-abc")
    return arcgis_client


def test_update_layer_renderer_posts_layer_level_update_definition(client: ArcGISClient) -> None:
    conn = SimpleNamespace(server_url="https://arc.test/portal", username="u")
    drawing_info = {
        "renderer": {
            "type": "simple",
            "symbol": {"type": "esriSMS", "color": [255, 0, 0, 255]},
        }
    }

    result = client.update_layer_renderer(
        conn=conn,
        layer_url="https://arc.test/server/rest/services/MyFs/FeatureServer/3",
        drawing_info=drawing_info,
    )

    assert result == {"success": True, "message": "Renderer aggiornato"}
    assert client._http.post.call_args.args[0] == (
        "https://arc.test/server/rest/admin/services/MyFs/FeatureServer/3/updateDefinition"
    )

    kwargs = client._http.post.call_args.kwargs
    assert kwargs["timeout"] == 60
    assert kwargs["data"]["f"] == "json"
    assert "token" not in kwargs["data"]
    assert json.loads(kwargs["data"]["updateDefinition"]) == {
        "drawingInfo": drawing_info
    }
    assert kwargs["headers"] == {
        "Referer": "https://arc.test/portal",
        "Authorization": "Bearer tok-abc",
    }


def test_update_layer_renderer_returns_error_message(client: ArcGISClient) -> None:
    client._http.post.return_value = _Response({
        "success": False,
        "error": {"message": "bad renderer"},
    })
    conn = SimpleNamespace(server_url="https://arc.test/portal", username="u")

    result = client.update_layer_renderer(
        conn=conn,
        layer_url="https://arc.test/server/rest/services/MyFs/FeatureServer/1",
        drawing_info={"renderer": {"type": "simple"}},
    )

    assert result == {"success": False, "message": "bad renderer"}
