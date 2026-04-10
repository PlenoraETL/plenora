"""
Test per ArcGISClient: add_features(), update_features(), delete_features(),
_write_batch() con batching e gestione errori.
"""

import pytest
from unittest.mock import MagicMock, call
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
    r.text = json.dumps(json_data)
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


def _make_features(n):
    """Generate n dummy Esri JSON features."""
    return [
        {"attributes": {"OBJECTID": i + 1, "NAME": f"feat_{i}"}}
        for i in range(n)
    ]


def _add_results(n, all_success=True):
    """Generate addResults/updateResults array."""
    results = []
    for i in range(n):
        if all_success:
            results.append({"objectId": i + 1, "success": True})
        else:
            results.append({"objectId": i + 1, "success": False,
                            "error": {"code": 1000, "description": f"error_{i}"}})
    return results


# =========================================================================
# add_features
# =========================================================================

class TestAddFeatures:
    """add_features with batching, success, errors."""

    def test_add_single_batch(self):
        http = MagicMock()
        features = _make_features(5)
        http.post.return_value = _resp({
            "addResults": _add_results(5),
        })

        c = _client(http)
        result = c.add_features(_conn(), "https://x/FS/0", features)

        assert result["success"] is True
        assert result["added_count"] == 5
        assert result["errors"] == []

    def test_add_multiple_batches(self):
        http = MagicMock()
        features = _make_features(2500)

        # Default batch size is 1000, so 3 batches: 1000+1000+500
        http.post.side_effect = [
            _resp({"addResults": _add_results(1000)}),
            _resp({"addResults": _add_results(1000)}),
            _resp({"addResults": _add_results(500)}),
        ]

        c = _client(http)
        result = c.add_features(_conn(), "https://x/FS/0", features)

        assert result["success"] is True
        assert result["added_count"] == 2500
        assert http.post.call_count == 3

    def test_add_custom_batch_size(self):
        http = MagicMock()
        features = _make_features(10)

        http.post.side_effect = [
            _resp({"addResults": _add_results(3)}),
            _resp({"addResults": _add_results(3)}),
            _resp({"addResults": _add_results(3)}),
            _resp({"addResults": _add_results(1)}),
        ]

        c = _client(http)
        result = c.add_features(_conn(), "https://x/FS/0", features, batch_size=3)

        assert result["added_count"] == 10
        assert http.post.call_count == 4

    def test_add_empty_features(self):
        http = MagicMock()
        c = _client(http)
        result = c.add_features(_conn(), "https://x/FS/0", [])

        assert result["success"] is True
        assert result["added_count"] == 0
        assert http.post.call_count == 0

    def test_add_partial_failure(self):
        http = MagicMock()
        features = _make_features(5)

        mixed_results = [
            {"objectId": 1, "success": True},
            {"objectId": 2, "success": True},
            {"objectId": 3, "success": False, "error": {"code": 1000, "description": "dup"}},
            {"objectId": 4, "success": True},
            {"objectId": 5, "success": False, "error": {"code": 1000, "description": "bad"}},
        ]
        http.post.return_value = _resp({"addResults": mixed_results})

        c = _client(http)
        result = c.add_features(_conn(), "https://x/FS/0", features)

        assert result["success"] is False
        assert result["added_count"] == 3
        assert len(result["errors"]) == 2

    def test_add_server_error_response(self):
        http = MagicMock()
        features = _make_features(3)

        http.post.return_value = _resp({
            "error": {"code": 500, "message": "Internal server error"},
        })

        c = _client(http)
        result = c.add_features(_conn(), "https://x/FS/0", features)

        assert result["success"] is False
        assert result["added_count"] == 0
        assert len(result["errors"]) == 1

    def test_add_request_exception(self):
        http = MagicMock()
        features = _make_features(3)
        http.post.side_effect = requests.RequestException("connection refused")

        c = _client(http)
        result = c.add_features(_conn(), "https://x/FS/0", features)

        assert result["success"] is False
        assert result["added_count"] == 0
        assert len(result["errors"]) == 1
        assert "connection refused" in result["errors"][0]["message"]

    def test_add_json_parse_error(self):
        http = MagicMock()
        features = _make_features(3)
        resp = MagicMock()
        resp.json.side_effect = ValueError("No JSON")
        resp.status_code = 200
        resp.text = "<html>error</html>"
        http.post.return_value = resp

        c = _client(http)
        result = c.add_features(_conn(), "https://x/FS/0", features)

        assert result["success"] is False
        assert result["added_count"] == 0
        assert len(result["errors"]) == 1

    def test_add_sends_referer_header(self):
        http = MagicMock()
        features = _make_features(2)
        http.post.return_value = _resp({"addResults": _add_results(2)})

        conn = _conn(server_url="https://myserver.com/arcgis")
        c = _client(http)
        c.add_features(conn, "https://x/FS/0", features)

        post_call = http.post.call_args
        headers = post_call.kwargs.get("headers") or post_call[1].get("headers", {})
        assert headers.get("Referer") == "https://myserver.com/arcgis"

    def test_add_token_included(self):
        http = MagicMock()
        features = _make_features(2)

        # Token auth
        http.post.side_effect = [
            _resp({"token": "tok_abc", "expires": 9999999999999}),  # authenticate
            _resp({"addResults": _add_results(2)}),                  # addFeatures
        ]

        conn = _conn(auth_type="token", username="u", password="p")
        c = _client(http)
        c.add_features(conn, "https://x/FS/0", features)

        # Second post = addFeatures
        add_call = http.post.call_args_list[1]
        data = add_call.kwargs.get("data") or add_call[1].get("data", {})
        assert data["token"] == "tok_abc"


# =========================================================================
# update_features
# =========================================================================

class TestUpdateFeatures:
    """update_features — uses _write_batch with updateFeatures."""

    def test_update_success(self):
        http = MagicMock()
        features = _make_features(3)
        http.post.return_value = _resp({
            "updateResults": _add_results(3),
        })

        c = _client(http)
        result = c.update_features(_conn(), "https://x/FS/0", features)

        assert result["success"] is True
        assert result["updated_count"] == 3
        assert result["errors"] == []

    def test_update_partial_failure(self):
        http = MagicMock()
        features = _make_features(3)
        mixed = [
            {"objectId": 1, "success": True},
            {"objectId": 2, "success": False, "error": {"description": "locked"}},
            {"objectId": 3, "success": True},
        ]
        http.post.return_value = _resp({"updateResults": mixed})

        c = _client(http)
        result = c.update_features(_conn(), "https://x/FS/0", features)

        assert result["success"] is False
        assert result["updated_count"] == 2
        assert len(result["errors"]) == 1

    def test_update_multiple_batches(self):
        http = MagicMock()
        features = _make_features(1500)
        http.post.side_effect = [
            _resp({"updateResults": _add_results(1000)}),
            _resp({"updateResults": _add_results(500)}),
        ]

        c = _client(http)
        result = c.update_features(_conn(), "https://x/FS/0", features)

        assert result["updated_count"] == 1500
        assert http.post.call_count == 2

    def test_update_posts_to_correct_endpoint(self):
        http = MagicMock()
        features = _make_features(1)
        http.post.return_value = _resp({"updateResults": _add_results(1)})

        c = _client(http)
        c.update_features(_conn(), "https://x/FS/0", features)

        post_call = http.post.call_args
        url = post_call.args[0] if post_call.args else post_call[0][0]
        assert url == "https://x/FS/0/updateFeatures"

    def test_update_empty_features(self):
        http = MagicMock()
        c = _client(http)
        result = c.update_features(_conn(), "https://x/FS/0", [])

        assert result["success"] is True
        assert result["updated_count"] == 0

    def test_update_server_error(self):
        http = MagicMock()
        features = _make_features(2)
        http.post.return_value = _resp({
            "error": {"code": 403, "message": "Not authorized"},
        })

        c = _client(http)
        result = c.update_features(_conn(), "https://x/FS/0", features)

        assert result["success"] is False
        assert result["updated_count"] == 0


# =========================================================================
# delete_features
# =========================================================================

class TestDeleteFeatures:
    """delete_features with objectIds, where clause, errors."""

    def test_delete_by_object_ids(self):
        http = MagicMock()
        http.post.return_value = _resp({
            "deleteResults": [
                {"objectId": 1, "success": True},
                {"objectId": 2, "success": True},
                {"objectId": 3, "success": True},
            ],
        })

        c = _client(http)
        result = c.delete_features(_conn(), "https://x/FS/0", object_ids=[1, 2, 3])

        assert result["success"] is True
        assert result["deleted_count"] == 3

        # Check objectIds in POST data
        post_call = http.post.call_args
        data = post_call.kwargs.get("data") or post_call[1].get("data", {})
        assert data["objectIds"] == "1,2,3"

    def test_delete_by_where(self):
        http = MagicMock()
        http.post.return_value = _resp({
            "deleteResults": [
                {"objectId": 10, "success": True},
            ],
        })

        c = _client(http)
        result = c.delete_features(_conn(), "https://x/FS/0", where="STATUS = 'old'")

        assert result["success"] is True
        assert result["deleted_count"] == 1

        post_call = http.post.call_args
        data = post_call.kwargs.get("data") or post_call[1].get("data", {})
        assert data["where"] == "STATUS = 'old'"

    def test_delete_no_criteria_raises(self):
        c = _client()
        with pytest.raises(ValueError, match="Specificare object_ids o where"):
            c.delete_features(_conn(), "https://x/FS/0")

    def test_delete_partial_failure(self):
        http = MagicMock()
        http.post.return_value = _resp({
            "deleteResults": [
                {"objectId": 1, "success": True},
                {"objectId": 2, "success": False, "error": {"description": "locked"}},
            ],
        })

        c = _client(http)
        result = c.delete_features(_conn(), "https://x/FS/0", object_ids=[1, 2])

        assert result["success"] is False
        assert result["deleted_count"] == 1
        assert len(result["errors"]) == 1

    def test_delete_server_error(self):
        http = MagicMock()
        http.post.return_value = _resp({
            "error": {"code": 500, "message": "Internal error"},
        })

        c = _client(http)
        result = c.delete_features(_conn(), "https://x/FS/0", object_ids=[1])

        assert result["success"] is False
        assert result["deleted_count"] == 0
        assert len(result["errors"]) == 1

    def test_delete_request_exception(self):
        http = MagicMock()
        http.post.side_effect = requests.RequestException("timeout")

        c = _client(http)
        result = c.delete_features(_conn(), "https://x/FS/0", object_ids=[1])

        assert result["success"] is False
        assert result["deleted_count"] == 0
        assert "timeout" in result["errors"][0]["message"]

    def test_delete_sends_token(self):
        http = MagicMock()

        http.post.side_effect = [
            _resp({"token": "del_tok", "expires": 9999999999999}),
            _resp({"deleteResults": [{"objectId": 1, "success": True}]}),
        ]

        conn = _conn(auth_type="token", username="u", password="p")
        c = _client(http)
        c.delete_features(conn, "https://x/FS/0", object_ids=[1])

        del_call = http.post.call_args_list[1]
        data = del_call.kwargs.get("data") or del_call[1].get("data", {})
        assert data["token"] == "del_tok"

    def test_delete_sends_referer(self):
        http = MagicMock()
        http.post.return_value = _resp({
            "deleteResults": [{"objectId": 1, "success": True}],
        })

        conn = _conn(server_url="https://myserver.com/arcgis")
        c = _client(http)
        c.delete_features(conn, "https://x/FS/0", object_ids=[1])

        post_call = http.post.call_args
        headers = post_call.kwargs.get("headers") or post_call[1].get("headers", {})
        assert headers.get("Referer") == "https://myserver.com/arcgis"

    def test_delete_posts_to_correct_endpoint(self):
        http = MagicMock()
        http.post.return_value = _resp({
            "deleteResults": [{"objectId": 1, "success": True}],
        })

        c = _client(http)
        c.delete_features(_conn(), "https://x/FS/0", object_ids=[1])

        post_call = http.post.call_args
        url = post_call.args[0] if post_call.args else post_call[0][0]
        assert url == "https://x/FS/0/deleteFeatures"


# =========================================================================
# _write_batch  (via add/update — edge cases)
# =========================================================================

class TestWriteBatch:
    """Edge cases for the shared _write_batch logic."""

    def test_batch_boundary_exact_multiple(self):
        """Features count == batch_size means exactly 1 batch."""
        http = MagicMock()
        features = _make_features(1000)
        http.post.return_value = _resp({"addResults": _add_results(1000)})

        c = _client(http)
        result = c.add_features(_conn(), "https://x/FS/0", features)

        assert result["added_count"] == 1000
        assert http.post.call_count == 1

    def test_batch_boundary_one_over(self):
        """1001 features = 2 batches with default size 1000."""
        http = MagicMock()
        features = _make_features(1001)
        http.post.side_effect = [
            _resp({"addResults": _add_results(1000)}),
            _resp({"addResults": _add_results(1)}),
        ]

        c = _client(http)
        result = c.add_features(_conn(), "https://x/FS/0", features)

        assert result["added_count"] == 1001
        assert http.post.call_count == 2

    def test_features_serialized_as_json_string(self):
        http = MagicMock()
        features = _make_features(2)
        http.post.return_value = _resp({"addResults": _add_results(2)})

        c = _client(http)
        c.add_features(_conn(), "https://x/FS/0", features)

        post_call = http.post.call_args
        data = post_call.kwargs.get("data") or post_call[1].get("data", {})
        # features should be a JSON string, not a list
        assert isinstance(data["features"], str)
        parsed = json.loads(data["features"])
        assert len(parsed) == 2

    def test_mixed_batch_results(self):
        """First batch succeeds, second batch has server error."""
        http = MagicMock()
        features = _make_features(1500)

        http.post.side_effect = [
            _resp({"addResults": _add_results(1000)}),
            _resp({"error": {"code": 500, "message": "disk full"}}),
        ]

        c = _client(http)
        result = c.add_features(_conn(), "https://x/FS/0", features)

        assert result["success"] is False
        assert result["added_count"] == 1000
        assert len(result["errors"]) == 1

    def test_all_batches_fail_with_exception(self):
        http = MagicMock()
        features = _make_features(500)
        http.post.side_effect = requests.RequestException("network down")

        c = _client(http)
        result = c.add_features(_conn(), "https://x/FS/0", features)

        assert result["success"] is False
        assert result["added_count"] == 0
