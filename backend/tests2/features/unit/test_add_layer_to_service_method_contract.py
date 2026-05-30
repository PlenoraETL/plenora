"""
Contract test BE-IMDF-006B — `ArcGISClient.add_layer_to_service` typed boundary.

Lock the public method shape post-promote:
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.features.arcgis_client import ArcGISClient
from app.core.features.arcgis_feature_write_methods import AddLayerResult


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# AddLayerResult — typed dataclass shape


def test_add_layer_result_fields():
    r = AddLayerResult(success=True, feature_count=5, errors=("e1", "e2"))
    assert r.success is True
    assert r.feature_count == 5
    assert r.errors == ("e1", "e2")
    assert isinstance(r.errors, tuple)


def test_add_layer_result_default_errors_empty_tuple():
    r = AddLayerResult(success=True, feature_count=1)
    assert r.errors == ()
    assert isinstance(r.errors, tuple)


def test_add_layer_result_is_frozen():
    """`AddLayerResult` e' immutabile: assignment post-init solleva
    `FrozenInstanceError`."""
    r = AddLayerResult(success=True, feature_count=1)
    with pytest.raises(FrozenInstanceError):
        r.success = False  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        r.feature_count = 99  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        r.errors = ("x",)  # type: ignore[misc]


def test_add_layer_result_equality_by_value():
    """Dataclass(frozen=True) ha equality by value e hash."""
    a = AddLayerResult(success=True, feature_count=1, errors=("e",))
    b = AddLayerResult(success=True, feature_count=1, errors=("e",))
    assert a == b
    assert hash(a) == hash(b)


# to_compat_dict — public dict adapter


def test_to_compat_dict_returns_legacy_shape():
    """Conversione al dict legacy preserva shape `{success,
    feature_count, errors: list}` per il wrapper IMDF."""
    r = AddLayerResult(success=True, feature_count=3, errors=("e1",))
    d = r.to_compat_dict()
    assert d == {"success": True, "feature_count": 3, "errors": ["e1"]}
    assert isinstance(d["errors"], list)  # NOTE: legacy list, not tuple


def test_to_compat_dict_empty_errors_returns_empty_list():
    r = AddLayerResult(success=True, feature_count=1)
    d = r.to_compat_dict()
    assert d == {"success": True, "feature_count": 1, "errors": []}


def test_to_compat_dict_failure_shape():
    r = AddLayerResult(success=False, feature_count=0, errors=("addToDefinition fallito",))
    d = r.to_compat_dict()
    assert d["success"] is False
    assert d["feature_count"] == 0
    assert d["errors"] == ["addToDefinition fallito"]


# ArcGISClient.add_layer_to_service — public method exists + signature


def test_add_layer_to_service_is_public_method():
    """Promoted as instance method on ArcGISClient (BE-IMDF-006B)."""
    assert hasattr(ArcGISClient, "add_layer_to_service")
    assert callable(ArcGISClient.add_layer_to_service)


def test_add_layer_to_service_signature_has_typed_kwargs():
    import inspect
    sig = inspect.signature(ArcGISClient.add_layer_to_service)
    params = sig.parameters
    assert "self" in params
    assert "conn" in params
    assert "service_url" in params
    assert "gdf" in params
    assert "layer_name" in params
    assert "layer_id" in params
    assert "srid" in params
    assert params["srid"].default == 4326
    # Return type annotated
    assert sig.return_annotation is AddLayerResult


# Public method end-to-end: returns AddLayerResult


def _make_response(payload: dict) -> MagicMock:
    resp = MagicMock(name="Response")
    resp.json = MagicMock(return_value=payload)
    return resp


def _make_polygon_gdf(rows: int = 2):
    import geopandas as gpd
    from shapely.geometry import Polygon

    geoms = [Polygon([(i, 0), (i + 1, 0), (i + 1, 1)]) for i in range(rows)]
    return gpd.GeoDataFrame(
        {"NAME": [f"f-{i}" for i in range(rows)]},
        geometry=geoms,
        crs="EPSG:4326",
    )


@pytest.fixture
def real_client():
    client = ArcGISClient.__new__(ArcGISClient)
    client._token_cache = {}
    client._http = MagicMock()
    client._http.post = MagicMock(return_value=_make_response({"success": True}))
    client.authenticate = MagicMock(return_value="tok")
    client._esri_geometry_type = MagicMock(return_value="esriGeometryPolygon")
    client._python_type_to_esri_field = MagicMock(
        side_effect=lambda col, dtype: {"name": col, "type": "esriFieldTypeString"}
    )
    client._default_drawing_info = MagicMock(return_value={"renderer": {}})
    client.dataframe_to_esri_features = MagicMock(return_value=[])
    client.add_features = MagicMock(return_value={"added_count": 4, "errors": []})
    client.add_dataframe_features = MagicMock(
        return_value={"added_count": 4, "errors": []}
    )
    return client


def test_add_layer_to_service_returns_AddLayerResult(real_client):
    conn = SimpleNamespace(server_url="https://arc.test", username="u")
    gdf = _make_polygon_gdf()

    result = real_client.add_layer_to_service(
        conn,
        "https://arc.test/server/rest/services/X/FeatureServer",
        gdf,
        "L",
        layer_id=1,
    )

    assert isinstance(result, AddLayerResult)
    assert result.success is True
    assert result.feature_count == 4
    assert result.errors == ()


def test_add_layer_to_service_failure_returns_typed_result(real_client):
    real_client._http.post.return_value = _make_response({
        "success": False, "error": {"code": 400, "message": "bad"},
    })
    conn = SimpleNamespace(server_url="https://arc.test", username="u")
    gdf = _make_polygon_gdf()

    result = real_client.add_layer_to_service(
        conn,
        "https://arc.test/server/rest/services/X/FeatureServer",
        gdf,
        "L",
        layer_id=0,
    )
    assert isinstance(result, AddLayerResult)
    assert result.success is False
    assert result.feature_count == 0
    assert len(result.errors) == 1
    assert "addToDefinition fallito" in result.errors[0]
    real_client.add_features.assert_not_called()


def test_add_layer_to_service_errors_is_tuple_not_list(real_client):
    """Errors propagati da `add_features` (lista legacy) sono
    convertiti a `tuple` immutabile in `AddLayerResult`."""
    real_client.add_dataframe_features.return_value = {
        "added_count": 0, "errors": ["a", "b"],
    }
    conn = SimpleNamespace(server_url="https://arc.test", username="u")
    gdf = _make_polygon_gdf()

    result = real_client.add_layer_to_service(
        conn,
        "https://arc.test/server/rest/services/X/FeatureServer",
        gdf,
        "L",
        layer_id=0,
    )
    assert isinstance(result.errors, tuple)
    assert result.errors == ("a", "b")

# (IMDF wrapper `publish_ops._add_layer_to_service` rimosso col cutover: il
# provider `add_layer_to_service` e' coperto dai test sopra; l'orchestrazione
# multi-layer e' nel writer condiviso, vedi test_arcgis_multilayer_write_contract.)
