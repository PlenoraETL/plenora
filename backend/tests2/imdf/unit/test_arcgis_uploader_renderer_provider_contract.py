"""IMDF publish delegates multi-layer write + renderers to the shared writer."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.imdf import arcgis_uploader


pytestmark = [pytest.mark.unit, pytest.mark.contract]

_FS_URL = "https://arc.test/server/rest/services/MyFs/FeatureServer"


class _Rows:
    def __len__(self) -> int:
        return 1


class _FakeWriter:
    """Writer condiviso finto: registra le chiamate, nessun ArcGIS reale."""

    def __init__(self):
        self.renderer_calls = []
        self.create_calls = []

    def create_service_with_layers(self, connection_id, service_name, layers,
                                   source_srid=None, folder_id=""):
        self.create_calls.append([l.layer_name for l in layers])
        return {
            "success": True, "service_url": _FS_URL, "item_id": "item-1",
            "layers": [{
                "id": 0, "name": layers[0].layer_name, "url": f"{_FS_URL}/0",
                "feature_count": 1, "errors": [],
            }],
            "feature_count": 1, "errors": [],
        }

    def apply_layer_renderers(self, connection_id, renderers):
        self.renderer_calls.append(dict(renderers))
        return {"applied": len(renderers), "errors": []}


def test_upload_imdf_layers_delegates_renderers_to_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = SimpleNamespace(server_url="https://arc.test/portal", username="u")
    monkeypatch.setattr(
        "app.core.imdf.arcgis_uploader.resolve_arcgis_connection",
        lambda connection_id: (conn, MagicMock(name="ArcGISProvider")),
    )
    # replace path: nessun FS esistente, nome disponibile.
    monkeypatch.setattr(
        "app.core.connections.arcgis_service_lifecycle.delete_existing_feature_service",
        lambda client, conn, fs_name: (0, 0),
    )
    monkeypatch.setattr(
        "app.core.connections.arcgis_service_discovery.is_service_name_available",
        lambda client, conn, fs_name: (True, None),
    )

    writer = _FakeWriter()
    result = arcgis_uploader.upload_imdf_layers(
        connection_id="conn-1",
        layers={"Localita": _Rows()},
        fs_name="MyFs",
        feature_writer=writer,
        apply_renderers=True,
        renderers_data={"Localita": {"renderer": {"type": "simple"}}},
    )

    assert result["success"] is True
    assert result["renderers_applied"] == 1
    # i layer create vanno al writer condiviso, non al client direttamente
    assert writer.create_calls == [["Localita"]]
    # il renderer e' mappato sull'URL del layer e passato al writer condiviso
    assert writer.renderer_calls == [{f"{_FS_URL}/0": {"renderer": {"type": "simple"}}}]
