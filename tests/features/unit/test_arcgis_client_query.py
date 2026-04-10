"""
Test per ArcGISClient: query_features() con paginazione, filtri,
e logica di retry token.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
import requests
import json

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


def _schema_response():
    """Standard layer schema response."""
    return {
        "id": 0,
        "name": "TestLayer",
        "geometryType": "esriGeometryPoint",
        "fields": [
            {"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OID",
             "nullable": False, "editable": False},
            {"name": "NAME", "type": "esriFieldTypeString", "alias": "Name",
             "length": 50, "nullable": True, "editable": True},
            {"name": "VALUE", "type": "esriFieldTypeDouble", "alias": "Value",
             "nullable": True, "editable": True},
        ],
        "extent": {"spatialReference": {"wkid": 4326}},
        "count": 100,
        "maxRecordCount": 10,
        "capabilities": "Query",
        "relationships": [],
    }


def _features(n, start_oid=1):
    """Generate n dummy features."""
    return [
        {
            "attributes": {"OBJECTID": start_oid + i, "NAME": f"item_{i}", "VALUE": float(i)},
            "geometry": {"x": 11.0 + i * 0.01, "y": 44.0},
        }
        for i in range(n)
    ]


# =========================================================================
# query_features  — basic
# =========================================================================

class TestQueryFeaturesBasic:
    """Single-page queries, field filtering, geometry."""

    def test_single_page_query(self):
        http = MagicMock()
        schema = _schema_response()
        feats = _features(5)

        http.get.side_effect = [
            _resp(schema),          # get_layer_schema
            _resp({"count": 100}),  # _get_feature_count (no count in schema fallback)
            _resp({"features": feats, "exceededTransferLimit": False}),
        ]
        # Fix: put count in schema so no fallback
        schema["count"] = 100
        http.get.side_effect = [
            _resp(schema),
            _resp({"features": feats, "exceededTransferLimit": False}),
        ]

        c = _client(http)
        rows, cols = c.query_features(_conn(), "https://x/FS/0")

        assert len(rows) == 5
        assert "OBJECTID" in cols
        assert "NAME" in cols
        assert "geometry" in cols

    def test_where_filter_passed(self):
        http = MagicMock()
        schema = _schema_response()
        http.get.side_effect = [
            _resp(schema),
            _resp({"features": _features(2), "exceededTransferLimit": False}),
        ]

        c = _client(http)
        c.query_features(_conn(), "https://x/FS/0", where="VALUE > 10")

        # The query call is the second GET
        query_call = http.get.call_args_list[1]
        params = query_call.kwargs.get("params") or query_call[1].get("params", {})
        assert params["where"] == "VALUE > 10"

    def test_out_fields_passed(self):
        http = MagicMock()
        schema = _schema_response()
        http.get.side_effect = [
            _resp(schema),
            _resp({"features": [
                {"attributes": {"NAME": "a"}, "geometry": {"x": 1, "y": 2}},
            ], "exceededTransferLimit": False}),
        ]

        c = _client(http)
        rows, cols = c.query_features(
            _conn(), "https://x/FS/0", out_fields="NAME"
        )

        # Only NAME + geometry expected
        assert "NAME" in cols
        assert "OBJECTID" not in cols

    def test_geometry_excluded(self):
        http = MagicMock()
        schema = _schema_response()
        http.get.side_effect = [
            _resp(schema),
            _resp({"features": [
                {"attributes": {"OBJECTID": 1, "NAME": "a", "VALUE": 1.0}},
            ], "exceededTransferLimit": False}),
        ]

        c = _client(http)
        rows, cols = c.query_features(
            _conn(), "https://x/FS/0", return_geometry=False
        )

        assert "geometry" not in cols

    def test_bounding_box_filter(self):
        http = MagicMock()
        schema = _schema_response()
        geom_filter = {
            "geometry": {"xmin": 10, "ymin": 40, "xmax": 15, "ymax": 45},
            "geometryType": "esriGeometryEnvelope",
            "spatialRel": "esriSpatialRelIntersects",
            "inSR": "4326",
        }
        http.get.side_effect = [
            _resp(schema),
            _resp({"features": _features(1), "exceededTransferLimit": False}),
        ]

        c = _client(http)
        c.query_features(
            _conn(), "https://x/FS/0", geometry_filter=geom_filter
        )

        query_call = http.get.call_args_list[1]
        params = query_call.kwargs.get("params") or query_call[1].get("params", {})
        assert "geometry" in params
        assert params["geometryType"] == "esriGeometryEnvelope"
        assert params["spatialRel"] == "esriSpatialRelIntersects"

    def test_error_in_query_raises_runtime_error(self):
        http = MagicMock()
        schema = _schema_response()
        http.get.side_effect = [
            _resp(schema),
            _resp({"error": {"message": "Invalid WHERE clause"}}),
        ]

        c = _client(http)
        with pytest.raises(RuntimeError, match="Invalid WHERE clause"):
            c.query_features(_conn(), "https://x/FS/0", where="BAD SYNTAX")

    def test_request_exception_raises(self):
        http = MagicMock()
        schema = _schema_response()
        http.get.side_effect = [
            _resp(schema),
            requests.RequestException("network error"),
        ]

        c = _client(http)
        with pytest.raises(ConnectionError, match="Errore query"):
            c.query_features(_conn(), "https://x/FS/0")

    def test_empty_features_returns_empty(self):
        http = MagicMock()
        schema = _schema_response()
        http.get.side_effect = [
            _resp(schema),
            _resp({"features": []}),
        ]

        c = _client(http)
        rows, cols = c.query_features(_conn(), "https://x/FS/0")
        assert rows == []
        assert cols == []


# =========================================================================
# query_features  — pagination
# =========================================================================

class TestQueryFeaturesPagination:
    """Multi-page queries with maxRecordCount."""

    def test_two_pages(self):
        http = MagicMock()
        schema = _schema_response()
        schema["maxRecordCount"] = 3

        page1_feats = _features(3, start_oid=1)
        page2_feats = _features(2, start_oid=4)

        http.get.side_effect = [
            _resp(schema),
            _resp({"features": page1_feats, "exceededTransferLimit": True}),
            _resp({"features": page2_feats, "exceededTransferLimit": False}),
        ]

        c = _client(http)
        rows, cols = c.query_features(_conn(), "https://x/FS/0")

        assert len(rows) == 5

    def test_stops_when_fewer_than_page_size(self):
        http = MagicMock()
        schema = _schema_response()
        schema["maxRecordCount"] = 10

        http.get.side_effect = [
            _resp(schema),
            _resp({"features": _features(7), "exceededTransferLimit": False}),
        ]

        c = _client(http)
        rows, _ = c.query_features(_conn(), "https://x/FS/0")
        assert len(rows) == 7
        # Only schema + one query call
        assert http.get.call_count == 2

    def test_max_features_limit(self):
        http = MagicMock()
        schema = _schema_response()
        schema["maxRecordCount"] = 5

        http.get.side_effect = [
            _resp(schema),
            _resp({"features": _features(5), "exceededTransferLimit": True}),
            _resp({"features": _features(5, start_oid=6), "exceededTransferLimit": True}),
        ]

        c = _client(http)
        rows, _ = c.query_features(_conn(), "https://x/FS/0", max_features=7)

        assert len(rows) == 7

    def test_exceeded_false_stops_pagination(self):
        http = MagicMock()
        schema = _schema_response()
        schema["maxRecordCount"] = 100

        http.get.side_effect = [
            _resp(schema),
            _resp({"features": _features(50), "exceededTransferLimit": False}),
        ]

        c = _client(http)
        rows, _ = c.query_features(_conn(), "https://x/FS/0")
        assert len(rows) == 50
        assert http.get.call_count == 2

    def test_result_offset_forwarded(self):
        http = MagicMock()
        schema = _schema_response()
        http.get.side_effect = [
            _resp(schema),
            _resp({"features": _features(3), "exceededTransferLimit": False}),
        ]

        c = _client(http)
        c.query_features(_conn(), "https://x/FS/0", result_offset=50)

        query_call = http.get.call_args_list[1]
        params = query_call.kwargs.get("params") or query_call[1].get("params", {})
        assert params["resultOffset"] == 50

    def test_three_pages_offsets_accumulate(self):
        http = MagicMock()
        schema = _schema_response()
        schema["maxRecordCount"] = 2

        http.get.side_effect = [
            _resp(schema),
            _resp({"features": _features(2, 1), "exceededTransferLimit": True}),
            _resp({"features": _features(2, 3), "exceededTransferLimit": True}),
            _resp({"features": _features(1, 5), "exceededTransferLimit": False}),
        ]

        c = _client(http)
        rows, _ = c.query_features(_conn(), "https://x/FS/0")

        assert len(rows) == 5
        # Check offsets in successive calls: 0, 2, 4
        query_calls = http.get.call_args_list[1:]  # skip schema
        offsets = [
            (c_.kwargs.get("params") or c_[1].get("params", {}))["resultOffset"]
            for c_ in query_calls
        ]
        assert offsets == [0, 2, 4]


# =========================================================================
# _find_oid_field
# =========================================================================

class TestFindOidField:
    """OID detection from fields list."""

    def test_finds_by_esri_type(self):
        c = _client()
        fields = [
            {"name": "MY_ID", "field_type": "esriFieldTypeOID"},
            {"name": "NAME", "field_type": "esriFieldTypeString"},
        ]
        assert c._find_oid_field(fields) == "MY_ID"

    def test_finds_by_name_fallback(self):
        c = _client()
        fields = [
            {"name": "OBJECTID", "field_type": "esriFieldTypeInteger"},
            {"name": "NAME", "field_type": "esriFieldTypeString"},
        ]
        assert c._find_oid_field(fields) == "OBJECTID"

    def test_finds_fid(self):
        c = _client()
        fields = [
            {"name": "FID", "field_type": "esriFieldTypeInteger"},
        ]
        assert c._find_oid_field(fields) == "FID"

    def test_returns_none_when_no_oid(self):
        c = _client()
        fields = [
            {"name": "NAME", "field_type": "esriFieldTypeString"},
        ]
        assert c._find_oid_field(fields) is None


# =========================================================================
# query_features  — order_by and out_sr
# =========================================================================

class TestQueryFeaturesParams:
    """order_by, out_sr, result_record_count."""

    def test_custom_order_by(self):
        http = MagicMock()
        schema = _schema_response()
        http.get.side_effect = [
            _resp(schema),
            _resp({"features": _features(1), "exceededTransferLimit": False}),
        ]

        c = _client(http)
        c.query_features(_conn(), "https://x/FS/0", order_by="NAME DESC")

        query_call = http.get.call_args_list[1]
        params = query_call.kwargs.get("params") or query_call[1].get("params", {})
        assert params["orderByFields"] == "NAME DESC"

    def test_default_order_by_oid(self):
        http = MagicMock()
        schema = _schema_response()
        http.get.side_effect = [
            _resp(schema),
            _resp({"features": _features(1), "exceededTransferLimit": False}),
        ]

        c = _client(http)
        c.query_features(_conn(), "https://x/FS/0")

        query_call = http.get.call_args_list[1]
        params = query_call.kwargs.get("params") or query_call[1].get("params", {})
        assert params["orderByFields"] == "OBJECTID ASC"

    def test_out_sr(self):
        http = MagicMock()
        schema = _schema_response()
        http.get.side_effect = [
            _resp(schema),
            _resp({"features": _features(1), "exceededTransferLimit": False}),
        ]

        c = _client(http)
        c.query_features(_conn(), "https://x/FS/0", out_sr=3857)

        query_call = http.get.call_args_list[1]
        params = query_call.kwargs.get("params") or query_call[1].get("params", {})
        assert params["outSR"] == 3857

    def test_custom_result_record_count(self):
        http = MagicMock()
        schema = _schema_response()
        http.get.side_effect = [
            _resp(schema),
            _resp({"features": _features(5), "exceededTransferLimit": False}),
        ]

        c = _client(http)
        c.query_features(_conn(), "https://x/FS/0", result_record_count=5)

        query_call = http.get.call_args_list[1]
        params = query_call.kwargs.get("params") or query_call[1].get("params", {})
        assert params["resultRecordCount"] == 5
