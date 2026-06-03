"""Contract: ArcGISFeatureWriteService.add_layer_to_service (shared writer).

Regression guard per il bug latente del collasso IMDF: `features_service.
create_service_with_layers` scrive il layer 0 via `writer.create_service_from_df`
e i layer 1..N via `writer.add_layer_to_service` — ma il WRITER reale non definiva
quel metodo, quindi un publish multi-layer scriveva solo il layer 0 (AttributeError
sui successivi). Il boundary test mockava il writer stesso, quindi non esercitava
mai `ArcGISFeatureWriteService` reale: questo test colma quella lacuna.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.features.arcgis_feature_writer import (
    ArcGISFeatureWriteService,
    LayerWriteResult,
)

pytestmark = [pytest.mark.unit, pytest.mark.features, pytest.mark.contract]


def test_real_writer_exposes_methods_create_service_with_layers_depends_on():
    """`features_service.create_service_with_layers` chiama questi due metodi sul
    writer reale: se mancano, il publish multi-layer fallisce a runtime
    (AttributeError). Guard diretto del bug."""
    for name in ("create_service_from_df", "add_layer_to_service"):
        assert hasattr(ArcGISFeatureWriteService, name), f"writer manca '{name}'"
        assert callable(getattr(ArcGISFeatureWriteService, name))


def _make_polygon_gdf(rows: int = 3):
    import geopandas as gpd
    from shapely.geometry import Polygon

    geoms = [Polygon([(i, 0), (i + 1, 0), (i + 1, 1)]) for i in range(rows)]
    return gpd.GeoDataFrame(
        {"NAME": [f"f-{i}" for i in range(rows)]},
        geometry=geoms,
        crs="EPSG:4326",
    )


class _RecordingClient:
    """Registra la chiamata client e ritorna un AddLayerResult-like."""

    def __init__(self):
        self.calls: list[tuple] = []

    def add_layer_to_service(self, conn, service_url, gdf, layer_name, layer_id, srid=4326):
        self.calls.append((service_url, type(gdf).__name__, layer_name, layer_id, srid))
        return SimpleNamespace(success=True, feature_count=len(gdf), errors=("w1",))


def test_writer_delegates_to_client_and_adapts_result():
    client = _RecordingClient()
    writer = ArcGISFeatureWriteService(client)
    gdf = _make_polygon_gdf(3)
    conn = SimpleNamespace(server_url="https://arc.test", username="u")

    res = writer.add_layer_to_service(
        conn, "https://arc.test/svc/FeatureServer", gdf, "Locale", 2, 4326,
    )

    # adatta AddLayerResult -> LayerWriteResult (feature_count + errors come list)
    assert isinstance(res, LayerWriteResult)
    assert res.feature_count == 3
    assert res.errors == ["w1"]
    # delega con l'ORDINE posizionale corretto del client reale:
    # (conn, service_url, gdf, layer_name, layer_id, srid) — non layer_name/df invertiti
    assert len(client.calls) == 1
    service_url, gdf_type, layer_name, layer_id, srid = client.calls[0]
    assert service_url == "https://arc.test/svc/FeatureServer"
    assert layer_name == "Locale"
    assert layer_id == 2
    assert srid == 4326
