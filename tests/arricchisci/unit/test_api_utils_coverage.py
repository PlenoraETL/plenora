"""
tests/arricchisci/unit/test_api_utils_coverage.py
=================================================
Unit tests for app.shared.api_utils.

Covers:
  - geojson_to_wkt (Point, Polygon, fallback to JSON string)
  - extract_json_value (dot notation, array index, wildcard *, GeoJSON auto-convert,
    None path, None intermediate, nested arrays, empty path)
  - get_oauth2_token (success, missing params, HTTP error, exception)
  - get_oauth2_client_credentials_token (success, missing params, HTTP error, exception)
  - prepare_headers (none, basic, bearer, api_key, oauth2_password,
    oauth2_client_credentials, custom headers)
"""

import base64
import json

import pytest
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit


# Override del fixture autouse `mock_reset` (root conftest.py) che fa skip
# se il mock_api_server non gira. Questi test sono puri unit, niente HTTP.
@pytest.fixture(autouse=True)
def mock_reset():
    yield


from app.shared.api_utils import (
    geojson_to_wkt,
    extract_json_value,
    get_oauth2_token,
    get_oauth2_client_credentials_token,
    prepare_headers,
    sanitize_for_log,
    sanitize_url_for_log,
)


# =============================================================================
# geojson_to_wkt
# =============================================================================


class TestGeojsonToWkt:

    @patch("shapely.geometry.shape")
    def test_point(self, mock_shape_fn):
        """Shapely converts a GeoJSON Point to WKT."""
        mock_geom = MagicMock()
        mock_geom.wkt = "POINT (12.4 41.9)"
        mock_shape_fn.return_value = mock_geom

        geom = {"type": "Point", "coordinates": [12.4, 41.9]}
        result = geojson_to_wkt(geom)
        assert result == "POINT (12.4 41.9)"
        mock_shape_fn.assert_called_once_with(geom)

    @patch("shapely.geometry.shape")
    def test_polygon(self, mock_shape_fn):
        mock_geom = MagicMock()
        mock_geom.wkt = "POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))"
        mock_shape_fn.return_value = mock_geom

        geom = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        }
        result = geojson_to_wkt(geom)
        assert result.startswith("POLYGON")

    @patch("shapely.geometry.shape", side_effect=Exception("bad geom"))
    def test_fallback_to_json_on_shapely_error(self, _mock):
        """When shapely fails, return JSON string."""
        geom = {"type": "Point", "coordinates": "invalid"}
        result = geojson_to_wkt(geom)
        parsed = json.loads(result)
        assert parsed["type"] == "Point"

    def test_fallback_to_json_no_shapely(self):
        """When shapely import fails inside the function, fallback works."""
        import sys
        # Temporarily remove shapely from sys.modules to force ImportError
        saved = sys.modules.get("shapely.geometry")
        sys.modules["shapely.geometry"] = None
        try:
            # The local import will fail, triggering the except branch
            geom = {"type": "MultiPolygon", "coordinates": []}
            result = geojson_to_wkt(geom)
            assert "MultiPolygon" in result
        finally:
            if saved is not None:
                sys.modules["shapely.geometry"] = saved
            else:
                sys.modules.pop("shapely.geometry", None)


# =============================================================================
# extract_json_value
# =============================================================================


class TestExtractJsonValue:

    def test_dot_notation_simple(self):
        data = {"a": {"b": {"c": 42}}}
        assert extract_json_value(data, "a.b.c") == 42

    def test_dot_notation_missing_key(self):
        data = {"a": {"b": 1}}
        assert extract_json_value(data, "a.x.y") is None

    def test_array_index(self):
        data = {"items": [{"id": 10}, {"id": 20}]}
        assert extract_json_value(data, "items[1].id") == 20

    def test_array_index_out_of_bounds(self):
        data = {"items": [1, 2]}
        assert extract_json_value(data, "items[5]") is None

    def test_wildcard_first_element(self):
        data = {"results": [{"name": "first"}, {"name": "second"}]}
        assert extract_json_value(data, "results[*].name") == "first"

    def test_wildcard_empty_list(self):
        data = {"results": []}
        assert extract_json_value(data, "results[*].name") is None

    def test_wildcard_non_list(self):
        """Wildcard on a dict should pass through (not a list)."""
        data = {"results": {"name": "val"}}
        result = extract_json_value(data, "results[*]")
        # * on non-list: current is not modified (stays dict), next part resolves
        assert result == {"name": "val"}

    def test_geojson_auto_convert_point(self):
        """GeoJSON geometry dicts get auto-converted to WKT."""
        geom = {"type": "Point", "coordinates": [12.0, 41.0]}
        data = {"location": geom}

        with patch("app.shared.api_utils.geojson_to_wkt", return_value="POINT (12 41)"):
            result = extract_json_value(data, "location")
        assert result == "POINT (12 41)"

    def test_geojson_auto_convert_polygon(self):
        geom = {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [0, 0]]]}
        data = {"shape": geom}

        with patch(
            "app.shared.api_utils.geojson_to_wkt",
            return_value="POLYGON ((0 0, 1 1, 0 0))",
        ):
            result = extract_json_value(data, "shape")
        assert "POLYGON" in result

    def test_none_path_returns_data(self):
        data = {"x": 1}
        assert extract_json_value(data, None) == {"x": 1}

    def test_empty_path_returns_data(self):
        data = {"x": 1}
        assert extract_json_value(data, "") == {"x": 1}

    def test_none_intermediate(self):
        data = {"a": None}
        assert extract_json_value(data, "a.b") is None

    def test_nested_arrays(self):
        data = {"a": [{"b": [{"c": 99}]}]}
        assert extract_json_value(data, "a[0].b[0].c") == 99

    def test_plain_value_no_geojson(self):
        data = {"name": "Roma"}
        assert extract_json_value(data, "name") == "Roma"

    def test_dict_without_geojson_type(self):
        """Dict that is not GeoJSON should be returned as-is."""
        data = {"meta": {"count": 5, "page": 1}}
        result = extract_json_value(data, "meta")
        assert result == {"count": 5, "page": 1}

    def test_non_dict_non_list_intermediate(self):
        """Trying to descend into a scalar returns None."""
        data = {"x": 42}
        assert extract_json_value(data, "x.y") is None

    def test_geojson_all_types_detected(self):
        """All GeoJSON geometry types trigger auto-convert."""
        types = [
            "Point", "Polygon", "MultiPolygon", "LineString",
            "MultiLineString", "MultiPoint", "GeometryCollection",
        ]
        for gtype in types:
            data = {"geo": {"type": gtype, "coordinates": []}}
            with patch(
                "app.shared.api_utils.geojson_to_wkt", return_value=f"WKT_{gtype}"
            ):
                result = extract_json_value(data, "geo")
            assert result == f"WKT_{gtype}", f"Failed for type {gtype}"


# =============================================================================
# get_oauth2_token
# =============================================================================


class TestGetOauth2Token:

    @patch("app.shared.api_utils.requests.post")
    def test_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "tok-123"}
        mock_post.return_value = mock_resp

        token = get_oauth2_token({
            "token_url": "https://auth.example.com/token",
            "username": "admin",
            "password": "secret",
        })
        assert token == "tok-123"
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs["data"]["grant_type"] == "password"

    @patch("app.shared.api_utils.requests.post")
    def test_http_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_post.return_value = mock_resp

        token = get_oauth2_token({
            "token_url": "https://auth.example.com/token",
            "username": "admin",
            "password": "wrong",
        })
        assert token is None

    def test_missing_token_url(self):
        token = get_oauth2_token({"username": "u", "password": "p"})
        assert token is None

    def test_missing_username(self):
        token = get_oauth2_token({
            "token_url": "https://auth.example.com/token",
            "password": "p",
        })
        assert token is None

    @patch("app.shared.api_utils.requests.post")
    def test_network_exception(self, mock_post):
        mock_post.side_effect = ConnectionError("DNS failure")

        token = get_oauth2_token({
            "token_url": "https://auth.example.com/token",
            "username": "admin",
            "password": "secret",
        })
        assert token is None


# =============================================================================
# get_oauth2_client_credentials_token
# =============================================================================


class TestGetOauth2ClientCredentialsToken:

    @patch("app.shared.api_utils.requests.post")
    def test_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "cc-tok-456"}
        mock_post.return_value = mock_resp

        token = get_oauth2_client_credentials_token({
            "token_url": "https://auth.example.com/token",
            "client_id": "my-client",
            "client_secret": "my-secret",
        })
        assert token == "cc-tok-456"
        call_data = mock_post.call_args.kwargs["data"]
        assert call_data["grant_type"] == "client_credentials"
        assert "scope" not in call_data

    @patch("app.shared.api_utils.requests.post")
    def test_success_with_scope(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "scoped-tok"}
        mock_post.return_value = mock_resp

        token = get_oauth2_client_credentials_token({
            "token_url": "https://auth.example.com/token",
            "client_id": "my-client",
            "client_secret": "my-secret",
            "scope": "read write",
        })
        assert token == "scoped-tok"
        call_data = mock_post.call_args.kwargs["data"]
        assert call_data["scope"] == "read write"

    @patch("app.shared.api_utils.requests.post")
    def test_http_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_post.return_value = mock_resp

        token = get_oauth2_client_credentials_token({
            "token_url": "https://auth.example.com/token",
            "client_id": "bad",
            "client_secret": "bad",
        })
        assert token is None

    def test_missing_token_url(self):
        token = get_oauth2_client_credentials_token({
            "client_id": "c", "client_secret": "s"
        })
        assert token is None

    def test_missing_client_id(self):
        token = get_oauth2_client_credentials_token({
            "token_url": "https://auth.example.com/token",
            "client_secret": "s",
        })
        assert token is None

    @patch("app.shared.api_utils.requests.post")
    def test_network_exception(self, mock_post):
        mock_post.side_effect = ConnectionError("timeout")

        token = get_oauth2_client_credentials_token({
            "token_url": "https://auth.example.com/token",
            "client_id": "c",
            "client_secret": "s",
        })
        assert token is None


# =============================================================================
# prepare_headers
# =============================================================================


class TestPrepareHeaders:

    def test_no_auth(self):
        headers = prepare_headers({"type": "none"})
        assert headers["Accept"] == "application/json"
        assert headers["User-Agent"] == "URBY-ETL/1.0"
        assert "Authorization" not in headers

    def test_basic_auth(self):
        headers = prepare_headers({
            "type": "basic_auth",
            "username": "user",
            "password": "pass",
        })
        expected = base64.b64encode(b"user:pass").decode()
        assert headers["Authorization"] == f"Basic {expected}"

    def test_basic_auth_alias(self):
        """'basic' type should also work."""
        headers = prepare_headers({
            "type": "basic",
            "username": "u",
            "password": "p",
        })
        assert headers["Authorization"].startswith("Basic ")

    def test_bearer_token(self):
        headers = prepare_headers({
            "type": "bearer",
            "token": "my-static-token",
        })
        assert headers["Authorization"] == "Bearer my-static-token"

    def test_token_type_alias(self):
        headers = prepare_headers({
            "type": "token",
            "access_token": "at-123",
        })
        assert headers["Authorization"] == "Bearer at-123"

    def test_bearer_empty_token(self):
        """No Authorization header when token is empty."""
        headers = prepare_headers({"type": "bearer"})
        assert "Authorization" not in headers

    def test_api_key_header(self):
        headers = prepare_headers({
            "type": "api_key",
            "location": "header",
            "key_name": "X-Custom-Key",
            "key_value": "abc123",
        })
        assert headers["X-Custom-Key"] == "abc123"

    def test_api_key_default_name(self):
        headers = prepare_headers({
            "type": "api_key",
            "key_value": "val",
        })
        assert headers["X-API-Key"] == "val"

    def test_api_key_query_param_not_in_headers(self):
        """api_key with location=query should NOT add to headers."""
        headers = prepare_headers({
            "type": "api_key",
            "location": "query",
            "key_name": "apikey",
            "key_value": "val",
        })
        assert "apikey" not in headers

    @patch("app.shared.api_utils.get_oauth2_token", return_value="oauth-tok")
    def test_oauth2_password(self, mock_get_token):
        headers = prepare_headers({
            "type": "oauth2_password",
            "token_url": "https://auth/token",
            "username": "u",
            "password": "p",
        })
        assert headers["Authorization"] == "Bearer oauth-tok"
        mock_get_token.assert_called_once()

    @patch("app.shared.api_utils.get_oauth2_token", return_value=None)
    def test_oauth2_password_no_token(self, mock_get_token):
        """When OAuth2 fails, no Authorization header is set."""
        headers = prepare_headers({
            "type": "oauth2_password",
            "token_url": "https://auth/token",
            "username": "u",
            "password": "p",
        })
        assert "Authorization" not in headers

    @patch(
        "app.shared.api_utils.get_oauth2_client_credentials_token",
        return_value="cc-tok",
    )
    def test_oauth2_client_credentials(self, mock_get_cc):
        headers = prepare_headers({
            "type": "oauth2_client_credentials",
            "token_url": "https://auth/token",
            "client_id": "c",
            "client_secret": "s",
        })
        assert headers["Authorization"] == "Bearer cc-tok"
        mock_get_cc.assert_called_once()

    @patch(
        "app.shared.api_utils.get_oauth2_client_credentials_token",
        return_value=None,
    )
    def test_oauth2_cc_no_token(self, mock_get_cc):
        headers = prepare_headers({
            "type": "oauth2_client_credentials",
            "token_url": "https://auth/token",
            "client_id": "c",
            "client_secret": "s",
        })
        assert "Authorization" not in headers

    def test_custom_headers_merge(self):
        headers = prepare_headers(
            {"type": "none"},
            custom_headers={"X-Custom": "value", "Accept": "text/xml"},
        )
        assert headers["X-Custom"] == "value"
        # custom overrides default Accept
        assert headers["Accept"] == "text/xml"

    def test_custom_headers_none(self):
        headers = prepare_headers({"type": "none"}, custom_headers=None)
        assert headers["Accept"] == "application/json"

    def test_unknown_auth_type(self):
        """Unknown auth type should not crash, just skip auth."""
        headers = prepare_headers({"type": "kerberos"})
        assert "Authorization" not in headers
        assert headers["Accept"] == "application/json"


# =============================================================================
# sanitize_for_log — redaction of sensitive dict/list values
# =============================================================================


class TestSanitizeForLog:

    def test_redacts_token_key(self):
        out = sanitize_for_log({"token": "abc123", "name": "foo"})
        assert out["token"] == "***REDACTED***"
        assert out["name"] == "foo"

    def test_redacts_api_key_variants(self):
        out = sanitize_for_log({
            "api_key": "x", "apiKey": "y", "API_KEY": "z", "x-api-key": "w",
        })
        for k in ("api_key", "apiKey", "API_KEY", "x-api-key"):
            assert out[k] == "***REDACTED***"

    def test_redacts_password_and_secret(self):
        out = sanitize_for_log({
            "password": "p", "passwd": "p2",
            "client_secret": "s", "private_key": "k",
        })
        assert all(v == "***REDACTED***" for v in out.values())

    def test_partial_match(self):
        # "user_password" contiene "password" → match parziale
        out = sanitize_for_log({"user_password": "p", "username": "u"})
        assert out["user_password"] == "***REDACTED***"
        assert out["username"] == "u"  # username non è sensitive

    def test_case_insensitive(self):
        out = sanitize_for_log({"Token": "x", "PASSWORD": "y"})
        assert out["Token"] == "***REDACTED***"
        assert out["PASSWORD"] == "***REDACTED***"

    def test_nested_dict(self):
        data = {"auth": {"token": "secret"}, "ok": "visible"}
        out = sanitize_for_log(data)
        assert out["auth"]["token"] == "***REDACTED***"
        assert out["ok"] == "visible"

    def test_nested_list(self):
        data = {"items": [{"token": "x"}, {"name": "y"}]}
        out = sanitize_for_log(data)
        assert out["items"][0]["token"] == "***REDACTED***"
        assert out["items"][1]["name"] == "y"

    def test_extra_key_via_auth(self):
        # auth.key_name dichiara una chiave custom da redactare
        out = sanitize_for_log({"my_custom_key": "v", "name": "x"},
                               auth={"key_name": "my_custom_key"})
        assert out["my_custom_key"] == "***REDACTED***"
        assert out["name"] == "x"

    def test_does_not_mutate_input(self):
        data = {"token": "abc"}
        sanitize_for_log(data)
        assert data["token"] == "abc"  # input intatto

    def test_empty_dict(self):
        assert sanitize_for_log({}) == {}

    def test_non_dict_returns_as_is(self):
        assert sanitize_for_log("hello") == "hello"
        assert sanitize_for_log(42) == 42
        assert sanitize_for_log(None) is None

    def test_none_auth(self):
        out = sanitize_for_log({"token": "x"}, auth=None)
        assert out["token"] == "***REDACTED***"

    def test_normal_dict_unchanged(self):
        data = {"name": "foo", "id": 1, "active": True}
        out = sanitize_for_log(data)
        assert out == data


# =============================================================================
# sanitize_url_for_log — redaction of URL credentials
# =============================================================================


class TestSanitizeUrlForLog:

    def test_redacts_token_in_query(self):
        out = sanitize_url_for_log("https://api.example.com/feature?token=SECRET&id=42")
        assert "SECRET" not in out
        assert "***REDACTED***" in out
        assert "id=42" in out

    def test_redacts_api_key_in_query(self):
        out = sanitize_url_for_log("https://api.example.com/?api_key=ABCD")
        assert "ABCD" not in out
        assert "api_key=***REDACTED***" in out

    def test_redacts_basic_auth(self):
        out = sanitize_url_for_log("https://user:pass@host.example.com/path")
        assert "user:pass" not in out
        assert "***REDACTED***@host.example.com" in out

    def test_no_query_string_unchanged(self):
        url = "https://api.example.com/feature/123"
        assert sanitize_url_for_log(url) == url

    def test_query_without_sensitive_keys_unchanged(self):
        url = "https://api.example.com/?id=42&name=foo"
        out = sanitize_url_for_log(url)
        assert "id=42" in out
        assert "name=foo" in out
        assert "***REDACTED***" not in out

    def test_multiple_sensitive_params(self):
        out = sanitize_url_for_log(
            "https://api.example.com/?token=tok_xyz999&password=pwdHunter&id=42"
        )
        # I valori sensibili NON devono apparire da nessuna parte
        assert "tok_xyz999" not in out
        assert "pwdHunter" not in out
        # Il parametro non sensibile resta intatto
        assert "id=42" in out
        # Entrambe le chiavi sensibili sono state redactate
        assert out.count("***REDACTED***") == 2

    def test_empty_url(self):
        assert sanitize_url_for_log("") == ""

    def test_none_url(self):
        # robusto a input non-string
        assert sanitize_url_for_log(None) in (None, "")

    def test_idempotent(self):
        url = "https://api.example.com/?token=X"
        once = sanitize_url_for_log(url)
        twice = sanitize_url_for_log(once)
        assert once == twice

    def test_path_with_token_substring_not_redacted(self):
        # "tokens" è plurale, è nel path non in query — NON deve essere redactato
        url = "https://api.example.com/tokens/list"
        out = sanitize_url_for_log(url)
        assert "tokens/list" in out
