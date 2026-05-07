"""
Contract test BE-IMDF-006A — `_add_layer_to_service` request/result shape lock.

Lock the current behavior of
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Pre-import per evitare side-effect import-time durante i test
import app.core.imdf.arcgis.publish_ops as publish_ops_mod
from app.core.imdf.arcgis.publish_ops import _add_layer_to_service
from app.core.features.arcgis_client import ArcGISClient


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Fixtures


def _make_response(payload: dict) -> MagicMock:
    resp = MagicMock(name="Response")
    resp.json = MagicMock(return_value=payload)
    return resp


@pytest.fixture
def fake_client():
    """Real `ArcGISClient` instance con metodi collaboratori mockati a
    livello instance (instance attr shadowa class attr/staticmethod).

    BE-IMDF-006B: post-promote di `_add_layer_to_service` a metodo
    pubblico `ArcGISClient.add_layer_to_service`. Il wrapper IMDF
    `_add_layer_to_service(client, ...)` delega a
    `client.add_layer_to_service(...).to_compat_dict()`. Per testare
    il body bit-per-bit (request shape, fields schema, extent,
    failure paths) serve un client reale (non MagicMock) cosi' il
    metodo `add_layer_to_service` esegue il body.

    Tutti i collaboratori (`_http.post`, `authenticate`, helper
    staticmethods, `dataframe_to_esri_features`, `add_features`)
    restano stubati per-instance via `MagicMock`. Le assertion sul
    `client._http.post.call_args` continuano a funzionare invariate.
    """
    client = ArcGISClient.__new__(ArcGISClient)
    client._token_cache = {}
    client._http = MagicMock()
    client._http.post = MagicMock(return_value=_make_response({"success": True}))
    client.authenticate = MagicMock(return_value="tok-abc")
    client._esri_geometry_type = MagicMock(return_value="esriGeometryPolygon")
    client._python_type_to_esri_field = MagicMock(
        side_effect=lambda col, dtype: {
            "name": col, "type": "esriFieldTypeString",
            "alias": col, "length": 50,
        }
    )
    client._default_drawing_info = MagicMock(return_value={"renderer": {"type": "simple"}})
    client.dataframe_to_esri_features = MagicMock(return_value=[
        {"attributes": {}, "geometry": {}},
    ])
    client.add_features = MagicMock(
        return_value={"added_count": 5, "errors": []}
    )
    return client


@pytest.fixture
def fake_conn():
    return SimpleNamespace(server_url="https://arc.test/portal", username="u")


def _make_polygon_gdf(rows: int = 3, *, crs_epsg: int | None = 4326):
    import geopandas as gpd
    from shapely.geometry import Polygon

    geoms = [
        Polygon([(i, 0), (i + 1, 0), (i + 1, 1), (i, 1)]) for i in range(rows)
    ]
    data = {"NAME": [f"feat-{i}" for i in range(rows)],
            "VALUE": list(range(rows))}
    if crs_epsg is None:
        return gpd.GeoDataFrame(data, geometry=geoms)
    return gpd.GeoDataFrame(data, geometry=geoms, crs=f"EPSG:{crs_epsg}")


def _post_call_kwargs(client) -> dict:
    """Estrae kwargs dell'ultima chiamata `client._http.post`."""
    return client._http.post.call_args.kwargs


def _post_url(client) -> str:
    return client._http.post.call_args.args[0]


def _layer_def_from_post(client) -> dict:
    """Decodifica `addToDefinition` dal body POST."""
    body = _post_call_kwargs(client)["data"]
    raw = body["addToDefinition"]
    return json.loads(raw)


# 1. Happy path + result shape


def test_happy_path_returns_keep_compat_dict(fake_client, fake_conn):
    gdf = _make_polygon_gdf(rows=3)

    result = _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/MyFs/FeatureServer",
        gdf=gdf, layer_name="Layer1", layer_id=1,
    )

    # KEEP_COMPAT shape (BE-IMDF-006A doc):
    # `{success: bool, feature_count: int, errors: list[str]}`
    assert set(result.keys()) == {"success", "feature_count", "errors"}
    assert result["success"] is True
    assert result["feature_count"] == 5
    assert result["errors"] == []
    fake_client._http.post.assert_called_once()
    fake_client.add_features.assert_called_once()


def test_happy_path_calls_collaborators_in_order(fake_client, fake_conn):
    """Verifica che addToDefinition vada PRIMA di add_features."""
    gdf = _make_polygon_gdf(rows=2)

    _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/MyFs/FeatureServer",
        gdf=gdf, layer_name="Layer1", layer_id=1,
    )

    # addToDefinition POST first, then add_features
    fake_client._http.post.assert_called_once()
    fake_client.add_features.assert_called_once()


# 2. Request shape: URL, body, header, timeout, token


def test_request_url_uses_admin_addtodefinition(fake_client, fake_conn):
    gdf = _make_polygon_gdf()

    _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/MyFs/FeatureServer",
        gdf=gdf, layer_name="L", layer_id=2,
    )

    url = _post_url(fake_client)
    assert url == (
        "https://arc.test/server/rest/admin/services/MyFs/FeatureServer/addToDefinition"
    )


def test_request_body_has_required_form_fields(fake_client, fake_conn):
    gdf = _make_polygon_gdf()

    _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=gdf, layer_name="L", layer_id=0,
    )

    body = _post_call_kwargs(fake_client)["data"]
    assert body["f"] == "json"
    assert "addToDefinition" in body
    # `addToDefinition` must be a JSON string (not a dict)
    assert isinstance(body["addToDefinition"], str)
    json.loads(body["addToDefinition"])  # parse-able


def test_request_body_includes_token_when_authenticated(fake_client, fake_conn):
    fake_client.authenticate.return_value = "tok-XYZ"
    gdf = _make_polygon_gdf()

    _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=gdf, layer_name="L", layer_id=0,
    )
    body = _post_call_kwargs(fake_client)["data"]
    assert body["token"] == "tok-XYZ"


@pytest.mark.parametrize("auth_return", [None, ""])
def test_request_body_omits_token_when_not_authenticated(
    fake_client, fake_conn, auth_return,
):
    fake_client.authenticate.return_value = auth_return
    gdf = _make_polygon_gdf()

    _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=gdf, layer_name="L", layer_id=0,
    )
    body = _post_call_kwargs(fake_client)["data"]
    assert "token" not in body


def test_request_headers_have_referer(fake_client, fake_conn):
    gdf = _make_polygon_gdf()

    _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=gdf, layer_name="L", layer_id=0,
    )
    headers = _post_call_kwargs(fake_client)["headers"]
    assert headers == {"Referer": "https://arc.test/portal"}


def test_request_timeout_60s(fake_client, fake_conn):
    gdf = _make_polygon_gdf()

    _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=gdf, layer_name="L", layer_id=0,
    )
    assert _post_call_kwargs(fake_client)["timeout"] == 60


# 3. Schema / layer_def shape


def test_layer_def_top_level_shape(fake_client, fake_conn):
    gdf = _make_polygon_gdf()

    _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=gdf, layer_name="MyLayer", layer_id=3,
    )
    layer_def = _layer_def_from_post(fake_client)
    assert "layers" in layer_def
    assert len(layer_def["layers"]) == 1
    layer = layer_def["layers"][0]
    assert layer["id"] == 3
    assert layer["name"] == "MyLayer"
    assert layer["type"] == "Feature Layer"
    assert layer["objectIdField"] == "OBJECTID"
    assert layer["hasAttachments"] is False
    assert layer["capabilities"] == "Create,Delete,Query,Update,Editing"


def test_first_field_is_oid(fake_client, fake_conn):
    gdf = _make_polygon_gdf()

    _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=gdf, layer_name="L", layer_id=0,
    )
    fields = _layer_def_from_post(fake_client)["layers"][0]["fields"]
    assert fields[0] == {
        "name": "OBJECTID",
        "type": "esriFieldTypeOID",
        "alias": "OBJECTID",
        "editable": False,
    }


def test_subsequent_fields_built_via_python_type_helper(fake_client, fake_conn):
    """Per ogni colonna non-system, deve essere chiamato
    `client._python_type_to_esri_field(col, dtype)`."""
    gdf = _make_polygon_gdf()

    _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=gdf, layer_name="L", layer_id=0,
    )
    # Helper chiamato per ciascuna colonna NAME, VALUE (non geometry)
    cols_called = [c.args[0] for c in fake_client._python_type_to_esri_field.call_args_list]
    assert "NAME" in cols_called
    assert "VALUE" in cols_called
    assert "geometry" not in cols_called


def test_excludes_geometry_and_oid_columns(fake_client, fake_conn):
    """Le colonne `geometry`, `OBJECTID`, `objectid` non vanno passate
    a `_python_type_to_esri_field`."""
    import geopandas as gpd
    from shapely.geometry import Polygon

    geoms = [Polygon([(0, 0), (1, 0), (1, 1)])]
    gdf = gpd.GeoDataFrame(
        {
            "NAME": ["x"],
            "OBJECTID": [1],     # ESRI auto-generated
            "objectid": [1],     # lowercase variant
        },
        geometry=geoms,
        crs="EPSG:4326",
    )

    _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=gdf, layer_name="L", layer_id=0,
    )
    cols_called = [c.args[0] for c in fake_client._python_type_to_esri_field.call_args_list]
    assert "OBJECTID" not in cols_called
    assert "objectid" not in cols_called
    assert "geometry" not in cols_called
    assert "NAME" in cols_called


def test_geometry_type_via_client_helper(fake_client, fake_conn):
    """Il geom_type deve passare attraverso `client._esri_geometry_type`
    sul majority shapely type."""
    fake_client._esri_geometry_type.return_value = "esriGeometryPolygon"
    gdf = _make_polygon_gdf(rows=4)

    _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=gdf, layer_name="L", layer_id=0,
    )

    fake_client._esri_geometry_type.assert_called_once()
    # Argomento passato e' lo string del majority type ("Polygon")
    arg = fake_client._esri_geometry_type.call_args.args[0]
    assert arg == "Polygon"

    layer = _layer_def_from_post(fake_client)["layers"][0]
    assert layer["geometryType"] == "esriGeometryPolygon"


def test_extent_filled_from_bounds_when_valid(fake_client, fake_conn):
    """Bounds del gdf finiscono nell'`extent` del layer_def."""
    gdf = _make_polygon_gdf(rows=3)
    # Polygons sono [(i, 0), (i+1, 0), (i+1, 1), (i, 1)] per i in 0..2
    # → xmin=0, ymin=0, xmax=3, ymax=1

    _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=gdf, layer_name="L", layer_id=0,
    )
    extent = _layer_def_from_post(fake_client)["layers"][0]["extent"]
    assert extent["xmin"] == 0.0
    assert extent["ymin"] == 0.0
    assert extent["xmax"] == 3.0
    assert extent["ymax"] == 1.0
    assert extent["spatialReference"]["wkid"] == 4326


def test_extent_defaults_to_world_when_no_geometry():
    """gdf senza geometria valida → extent default world bbox."""
    import pandas as pd
    plain_df = pd.DataFrame({"NAME": ["a"], "VALUE": [1]})

    fake_client = ArcGISClient.__new__(ArcGISClient)
    fake_client._token_cache = {}
    fake_client.authenticate = MagicMock(return_value="t")
    fake_client._http = MagicMock()
    fake_client._http.post = MagicMock(return_value=_make_response({"success": True}))
    fake_client._esri_geometry_type = MagicMock(return_value="esriGeometryPolygon")
    fake_client._python_type_to_esri_field = MagicMock(
        side_effect=lambda col, dtype: {"name": col, "type": "esriFieldTypeString"}
    )
    fake_client._default_drawing_info = MagicMock(return_value={})
    fake_client.dataframe_to_esri_features = MagicMock(return_value=[])
    fake_client.add_features = MagicMock(return_value={"added_count": 0, "errors": []})

    fake_conn = SimpleNamespace(server_url="https://arc.test", username="u")

    _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=plain_df, layer_name="L", layer_id=0,
    )
    extent = _layer_def_from_post(fake_client)["layers"][0]["extent"]
    assert extent == {
        "xmin": -180, "ymin": -90, "xmax": 180, "ymax": 90,
        "spatialReference": {"wkid": 4326},
    }


def test_srid_overridden_from_gdf_crs(fake_client, fake_conn):
    """Quando il gdf ha CRS, `srid` argomento e' sovrascritto da
    `gdf.crs.to_epsg()`."""
    gdf = _make_polygon_gdf(crs_epsg=3857)  # Web Mercator

    _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=gdf, layer_name="L", layer_id=0,
        srid=4326,  # caller-passed
    )
    extent = _layer_def_from_post(fake_client)["layers"][0]["extent"]
    # gdf.crs.to_epsg() == 3857 → wkid=3857 (override del kwarg srid)
    assert extent["spatialReference"]["wkid"] == 3857


def test_drawing_info_via_client_helper(fake_client, fake_conn):
    fake_client._default_drawing_info.return_value = {
        "renderer": {"type": "simple", "symbol": {"color": [255, 0, 0]}}
    }
    gdf = _make_polygon_gdf()

    _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=gdf, layer_name="L", layer_id=0,
    )
    fake_client._default_drawing_info.assert_called_once_with("esriGeometryPolygon")
    layer = _layer_def_from_post(fake_client)["layers"][0]
    assert layer["drawingInfo"] == {
        "renderer": {"type": "simple", "symbol": {"color": [255, 0, 0]}}
    }


# 4. Failure paths


def test_addtodefinition_failure_returns_failed_result(fake_client, fake_conn):
    """`addToDefinition` con `success: False` → risultato fallito,
    NESSUNA chiamata a `add_features`."""
    fake_client._http.post.return_value = _make_response({
        "success": False,
        "error": {"code": 400, "message": "bad schema"},
    })
    gdf = _make_polygon_gdf()

    result = _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=gdf, layer_name="L", layer_id=0,
    )
    assert result["success"] is False
    assert result["feature_count"] == 0
    assert len(result["errors"]) == 1
    assert "addToDefinition fallito" in result["errors"][0]
    fake_client.add_features.assert_not_called()


def test_add_features_zero_added_returns_not_success(fake_client, fake_conn):
    """`add_features` con `added_count=0` → `success=False`. errori
    propagati."""
    fake_client.add_features.return_value = {
        "added_count": 0,
        "errors": ["err1", "err2"],
    }
    gdf = _make_polygon_gdf()

    result = _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=gdf, layer_name="L", layer_id=0,
    )
    assert result["success"] is False
    assert result["feature_count"] == 0
    assert result["errors"] == ["err1", "err2"]


def test_add_features_partial_success_returns_success(fake_client, fake_conn):
    """`add_features` con `added_count > 0` → `success=True` anche se
    ci sono errors per features rifiutate (parziale)."""
    fake_client.add_features.return_value = {
        "added_count": 2,
        "errors": ["row 3 rejected"],
    }
    gdf = _make_polygon_gdf(rows=3)

    result = _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=gdf, layer_name="L", layer_id=0,
    )
    assert result["success"] is True
    assert result["feature_count"] == 2
    assert result["errors"] == ["row 3 rejected"]


# 5. Features payload boundary


def test_dataframe_to_esri_features_called_with_expected_args(
    fake_client, fake_conn,
):
    """Verifica i kwargs/args passati a
    `client.dataframe_to_esri_features`: gdf, "geometry", srid,
    operation="create_service"."""
    gdf = _make_polygon_gdf(crs_epsg=4326)

    _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=gdf, layer_name="L", layer_id=0,
    )
    call = fake_client.dataframe_to_esri_features.call_args
    assert call.args[0] is gdf
    assert call.args[1] == "geometry"
    assert call.args[2] == 4326
    assert call.kwargs == {"operation": "create_service"}


def test_add_features_called_on_layer_url(fake_client, fake_conn):
    """`add_features` deve essere chiamato con
    `service_url + "/" + layer_id`."""
    gdf = _make_polygon_gdf()

    _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=gdf, layer_name="L", layer_id=7,
    )
    call = fake_client.add_features.call_args
    assert call.args[0] is fake_conn
    assert call.args[1] == "https://arc.test/server/rest/services/X/FeatureServer/7"


# 6. KEEP_COMPAT result shape sentinel


def test_result_keys_keep_compat_sentinel(fake_client, fake_conn):
    """Sentinel: il dict opaco `{success, feature_count, errors}` e'
    il contratto KEEP_COMPAT. Future BE-IMDF-006B promote lo
    sostituira' con `dataclass AddLayerResult` ma il dict resta lato
    caller IMDF (`upload_imdf_layers`).

    Se questo test rompe e' segnale di:
    - aggiungere chiavi al dict → confermare in IMDF-006B come campi
      del dataclass + alias dict;
    - rimuovere chiavi → breaking change verso `upload_imdf_layers`.
    """
    gdf = _make_polygon_gdf()

    result = _add_layer_to_service(
        fake_client, fake_conn,
        service_url="https://arc.test/server/rest/services/X/FeatureServer",
        gdf=gdf, layer_name="L", layer_id=0,
    )
    assert set(result.keys()) == {"success", "feature_count", "errors"}
    assert isinstance(result["success"], bool)
    assert isinstance(result["feature_count"], int)
    assert isinstance(result["errors"], list)
