"""
Test per ArcGISClient: list_layers(), _build_layer_urls(), get_layer_schema().
"""

import pytest
from unittest.mock import MagicMock
import requests

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resp(json_data, status_code=200):
    r = MagicMock()
    r.json.return_value = json_data
    r.status_code = status_code
    r.text = ""
    return r


def _conn(**kw):
    from app.core.features.arcgis_client import ArcGISConnectionInfo
    defaults = dict(
        server_url="https://gis.example.com/arcgis",
        server_type="arcgis_server",
        auth_type="public",
        username="",
        password="",
    )
    defaults.update(kw)
    return ArcGISConnectionInfo(**defaults)


def _client(http=None):
    from app.core.features.arcgis_client import ArcGISClient
    return ArcGISClient(http_client=http or MagicMock())


# =========================================================================
# _build_layer_urls
# =========================================================================

class TestBuildLayerUrls:
    """URL candidate list for layer discovery."""

    def test_feature_server_url(self):
        c = _client()
        urls = c._build_layer_urls("https://x/rest/services/Svc/FeatureServer")
        assert urls[0] == "https://x/rest/services/Svc/FeatureServer"
        assert urls[1] == "https://x/rest/services/Svc/MapServer"

    def test_map_server_url(self):
        c = _client()
        urls = c._build_layer_urls("https://x/rest/services/Svc/MapServer")
        # FeatureServer tried first
        assert urls[0] == "https://x/rest/services/Svc/FeatureServer"
        assert urls[1] == "https://x/rest/services/Svc/MapServer"

    def test_no_suffix(self):
        c = _client()
        urls = c._build_layer_urls("https://x/rest/services/Svc")
        assert urls[0] == "https://x/rest/services/Svc/FeatureServer"
        assert urls[1] == "https://x/rest/services/Svc/MapServer"

    def test_trailing_slash_stripped(self):
        c = _client()
        urls = c._build_layer_urls("https://x/rest/services/Svc/FeatureServer/")
        assert urls[0] == "https://x/rest/services/Svc/FeatureServer"

    def test_returns_two_candidates(self):
        c = _client()
        for url in [
            "https://x/FeatureServer",
            "https://x/MapServer",
            "https://x/SomeSvc",
        ]:
            urls = c._build_layer_urls(url)
            assert len(urls) == 2


# =========================================================================
# list_layers
# =========================================================================

class TestListLayers:
    """list_layers with URL fallback and error handling."""

    def _layer_data(self):
        return {
            "layers": [
                {
                    "id": 0,
                    "name": "Buildings",
                    "geometryType": "esriGeometryPolygon",
                    "minScale": 50000,
                    "maxScale": 0,
                    "count": 1234,
                },
                {
                    "id": 1,
                    "name": "Roads",
                    "geometryType": "esriGeometryPolyline",
                },
            ],
            "tables": [
                {
                    "id": 2,
                    "name": "Metadata",
                },
            ],
        }

    def test_success_returns_layers_and_tables(self):
        http = MagicMock()
        http.get.return_value = _resp(self._layer_data())

        c = _client(http)
        result = c.list_layers(_conn(), "https://x/FeatureServer")

        assert len(result) == 3
        assert result[0]["name"] == "Buildings"
        assert result[0]["geometry_type"] == "esriGeometryPolygon"
        assert result[0]["feature_count"] == 1234
        assert result[0]["url"] == "https://x/FeatureServer/0"

        # Table entry
        tbl = result[2]
        assert tbl["name"] == "Metadata"
        assert tbl["geometry_type"] == ""
        assert tbl["url"] == "https://x/FeatureServer/2"

    def test_fallback_to_second_url_on_error(self):
        http = MagicMock()
        error_resp = _resp({"error": {"message": "Service not found"}})
        ok_resp = _resp(self._layer_data())
        http.get.side_effect = [error_resp, ok_resp]

        c = _client(http)
        result = c.list_layers(_conn(), "https://x/FeatureServer")

        assert len(result) == 3
        assert http.get.call_count == 2

    def test_fallback_on_request_exception(self):
        http = MagicMock()
        http.get.side_effect = [
            requests.RequestException("timeout"),
            _resp(self._layer_data()),
        ]

        c = _client(http)
        result = c.list_layers(_conn(), "https://x/FeatureServer")
        assert len(result) == 3

    def test_all_urls_fail_raises_connection_error(self):
        http = MagicMock()
        http.get.return_value = _resp({"error": {"message": "Not found"}})

        c = _client(http)
        with pytest.raises(ConnectionError, match="Not found"):
            c.list_layers(_conn(), "https://x/FeatureServer")

    def test_all_urls_exception_raises(self):
        http = MagicMock()
        http.get.side_effect = requests.RequestException("down")

        c = _client(http)
        with pytest.raises(ConnectionError):
            c.list_layers(_conn(), "https://x/Svc")

    def test_empty_layers_returns_empty(self):
        http = MagicMock()
        http.get.return_value = _resp({"layers": [], "tables": []})

        c = _client(http)
        result = c.list_layers(_conn(), "https://x/FeatureServer")
        assert result == []

    def test_token_passed_to_params(self):
        http = MagicMock()
        http.post.return_value = _resp({"token": "tk1", "expires": 9999999999999})
        http.get.return_value = _resp({"layers": [], "tables": []})

        c = _client(http)
        conn = _conn(auth_type="token", username="u", password="p")
        c.list_layers(conn, "https://x/FeatureServer")

        get_call = http.get.call_args
        params = get_call.kwargs.get("params") or get_call[1].get("params", {})
        assert params["token"] == "tk1"


# =========================================================================
# get_layer_schema
# =========================================================================

class TestGetLayerSchema:
    """Field mapping, geometry detection, feature count."""

    def _schema_data(self, **overrides):
        data = {
            "id": 0,
            "name": "Buildings",
            "geometryType": "esriGeometryPolygon",
            "fields": [
                {"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OID",
                 "nullable": False, "editable": False},
                {"name": "NAME", "type": "esriFieldTypeString", "alias": "Name",
                 "length": 100, "nullable": True, "editable": True},
                {"name": "AREA_SQM", "type": "esriFieldTypeDouble", "alias": "Area",
                 "nullable": True, "editable": True},
                {"name": "BUILT_DATE", "type": "esriFieldTypeDate", "alias": "Built",
                 "nullable": True, "editable": True},
            ],
            "extent": {
                "spatialReference": {"wkid": 4326, "latestWkid": 4326},
            },
            "count": 5000,
            "capabilities": "Query,Editing",
            "maxRecordCount": 2000,
            "description": "Building footprints",
            "relationships": [],
        }
        data.update(overrides)
        return data

    def test_basic_schema(self):
        http = MagicMock()
        http.get.return_value = _resp(self._schema_data())

        c = _client(http)
        schema = c.get_layer_schema(_conn(), "https://x/FS/0")

        assert schema["layer_id"] == 0
        assert schema["name"] == "Buildings"
        assert schema["geometry_type"] == "esriGeometryPolygon"
        assert schema["feature_count"] == 5000
        assert schema["max_record_count"] == 2000

    def test_field_type_mapping(self):
        http = MagicMock()
        http.get.return_value = _resp(self._schema_data())

        c = _client(http)
        schema = c.get_layer_schema(_conn(), "https://x/FS/0")

        fields = {f["name"]: f for f in schema["fields"]}
        assert fields["OBJECTID"]["urby_type"] == "int"
        assert fields["NAME"]["urby_type"] == "str"
        assert fields["AREA_SQM"]["urby_type"] == "real"
        assert fields["BUILT_DATE"]["urby_type"] == "date"

    def test_geometry_type_with_srid(self):
        http = MagicMock()
        http.get.return_value = _resp(self._schema_data())

        c = _client(http)
        schema = c.get_layer_schema(_conn(), "https://x/FS/0")

        assert schema["geometry_type_urby"] == "geometry:polygon:4326"

    def test_geometry_point_type(self):
        http = MagicMock()
        data = self._schema_data(geometryType="esriGeometryPoint")
        http.get.return_value = _resp(data)

        c = _client(http)
        schema = c.get_layer_schema(_conn(), "https://x/FS/0")
        assert schema["geometry_type_urby"] == "geometry:point:4326"

    def test_geometry_line_type(self):
        http = MagicMock()
        data = self._schema_data(geometryType="esriGeometryPolyline")
        http.get.return_value = _resp(data)

        c = _client(http)
        schema = c.get_layer_schema(_conn(), "https://x/FS/0")
        assert schema["geometry_type_urby"] == "geometry:line:4326"

    def test_no_geometry_type(self):
        http = MagicMock()
        data = self._schema_data(geometryType="")
        http.get.return_value = _resp(data)

        c = _client(http)
        schema = c.get_layer_schema(_conn(), "https://x/FS/0")
        assert schema["geometry_type_urby"] == "geometry"

    def test_feature_count_fallback_query(self):
        """When count is not in the schema response, query for it."""
        http = MagicMock()
        data = self._schema_data()
        del data["count"]
        schema_resp = _resp(data)
        count_resp = _resp({"count": 9999})
        http.get.side_effect = [schema_resp, count_resp]

        c = _client(http)
        schema = c.get_layer_schema(_conn(), "https://x/FS/0")
        assert schema["feature_count"] == 9999

    def test_columns_include_geometry(self):
        http = MagicMock()
        http.get.return_value = _resp(self._schema_data())

        c = _client(http)
        schema = c.get_layer_schema(_conn(), "https://x/FS/0")

        col_names = [col["name"] for col in schema["columns"]]
        assert "geometry" in col_names
        geom_col = [col for col in schema["columns"] if col["name"] == "geometry"][0]
        assert geom_col["type"].startswith("geometry:polygon")

    def test_columns_no_geometry_for_table(self):
        http = MagicMock()
        data = self._schema_data(geometryType="")
        http.get.return_value = _resp(data)

        c = _client(http)
        schema = c.get_layer_schema(_conn(), "https://x/FS/0")

        col_names = [col["name"] for col in schema["columns"]]
        assert "geometry" not in col_names

    def test_error_in_layer_raises(self):
        http = MagicMock()
        http.get.return_value = _resp({
            "error": {"message": "Layer not found"},
        })

        c = _client(http)
        with pytest.raises(ConnectionError, match="Layer not found"):
            c.get_layer_schema(_conn(), "https://x/FS/0")

    def test_request_exception_raises(self):
        http = MagicMock()
        http.get.side_effect = requests.RequestException("timeout")

        c = _client(http)
        with pytest.raises(ConnectionError, match="Errore connessione al layer"):
            c.get_layer_schema(_conn(), "https://x/FS/0")

    def test_relationships_mapped(self):
        http = MagicMock()
        data = self._schema_data(relationships=[
            {
                "id": 0,
                "name": "BuildingToFloors",
                "relatedTableId": 1,
                "cardinality": "esriRelCardinalityOneToMany",
                "role": "esriRelRoleOrigin",
                "keyField": "BUILDING_ID",
                "keyFieldInRelationshipTable": "PARENT_ID",
                "composite": True,
            },
        ])
        http.get.return_value = _resp(data)

        c = _client(http)
        schema = c.get_layer_schema(_conn(), "https://x/FS/0")

        rels = schema["relationships"]
        assert len(rels) == 1
        assert rels[0]["cardinality"] == "1:N"
        assert rels[0]["role"] == "origin"
        assert rels[0]["related_url"] == "https://x/FS/1"
        assert rels[0]["composite"] is True

    def test_spatial_reference_latest_wkid_preferred(self):
        http = MagicMock()
        data = self._schema_data()
        data["extent"]["spatialReference"] = {"wkid": 102100, "latestWkid": 3857}
        http.get.return_value = _resp(data)

        c = _client(http)
        schema = c.get_layer_schema(_conn(), "https://x/FS/0")
        assert schema["geometry_type_urby"] == "geometry:polygon:3857"

    def test_unknown_field_type_defaults_to_str(self):
        http = MagicMock()
        data = self._schema_data()
        data["fields"].append({
            "name": "WEIRD", "type": "esriFieldTypeFutureType", "alias": "Weird",
        })
        http.get.return_value = _resp(data)

        c = _client(http)
        schema = c.get_layer_schema(_conn(), "https://x/FS/0")
        weird = [f for f in schema["fields"] if f["name"] == "WEIRD"][0]
        assert weird["urby_type"] == "str"
