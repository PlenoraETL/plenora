"""
Unit tests for ArcGISClient._resolve_token_url() and _discover_token_url().
"""

import pytest
from unittest.mock import MagicMock

from app.core.features.arcgis_client import ArcGISClient, ArcGISConnectionInfo

pytestmark = pytest.mark.unit


# =============================================================================
# Helpers
# =============================================================================

def _make_conn(**overrides):
    defaults = {
        "server_url": "https://myserver.example.com/arcgis/rest/services",
        "server_type": "arcgis_server",
        "auth_type": "token",
        "username": "user1",
        "password": "pass1",
        "client_id": "",
        "client_secret": "",
        "token_url": "",
    }
    defaults.update(overrides)
    return ArcGISConnectionInfo(**defaults)


def _mock_response(json_data, status_code=200):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.status_code = status_code
    return resp


# =============================================================================
# _resolve_token_url — custom URL
# =============================================================================

class TestResolveTokenUrlCustom:
    """Test _resolve_token_url() when a custom token_url is provided."""

    def test_custom_url_returned_as_is(self):
        http = MagicMock()
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            token_url="https://custom.example.com/tokens/generateToken"
        )
        result = client._resolve_token_url(conn)
        assert result == "https://custom.example.com/tokens/generateToken"
        http.get.assert_not_called()

    def test_custom_url_takes_priority_over_arcgis_com(self):
        http = MagicMock()
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            server_url="https://services.arcgis.com/xxx",
            token_url="https://custom.example.com/token",
        )
        result = client._resolve_token_url(conn)
        assert result == "https://custom.example.com/token"


# =============================================================================
# _resolve_token_url — arcgis.com auto-detect
# =============================================================================

class TestResolveTokenUrlArcGISCom:
    """Test _resolve_token_url() auto-detection for arcgis.com URLs."""

    def test_arcgis_com_in_url(self):
        http = MagicMock()
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            server_url="https://services.arcgis.com/xxx/arcgis/rest/services",
            server_type="arcgis_server",
            token_url="",
        )
        result = client._resolve_token_url(conn)
        assert result == "https://services.arcgis.com/sharing/rest/generateToken"

    def test_org_maps_arcgis_com(self):
        http = MagicMock()
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            server_url="https://myorg.maps.arcgis.com/sharing/rest",
            token_url="",
        )
        result = client._resolve_token_url(conn)
        assert result == "https://myorg.maps.arcgis.com/sharing/rest/generateToken"

    def test_arcgis_com_preserves_scheme(self):
        http = MagicMock()
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            server_url="http://services.arcgis.com/xxx",
            token_url="",
        )
        result = client._resolve_token_url(conn)
        assert result.startswith("http://")

    def test_arcgis_com_case_insensitive(self):
        http = MagicMock()
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            server_url="https://services.ARCGIS.COM/xxx",
            token_url="",
        )
        result = client._resolve_token_url(conn)
        assert "sharing/rest/generateToken" in result


# =============================================================================
# _resolve_token_url — server_type arcgis_online without arcgis.com in URL
# =============================================================================

class TestResolveTokenUrlOnlineType:
    """Test _resolve_token_url() with server_type=arcgis_online but non-arcgis.com URL."""

    def test_online_type_with_arcgis_com_domain(self):
        http = MagicMock()
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            server_url="https://org.maps.arcgis.com",
            server_type="arcgis_online",
            token_url="",
        )
        result = client._resolve_token_url(conn)
        # arcgis.com in url triggers the first branch
        assert "sharing/rest/generateToken" in result

    def test_online_type_non_arcgis_domain_falls_to_default(self):
        """If server_type is arcgis_online but URL has no arcgis.com, use www.arcgis.com."""
        http = MagicMock()
        # Discovery returns nothing
        http.get.return_value = _mock_response({})
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            server_url="https://custom-portal.example.com/sharing",
            server_type="arcgis_online",
            token_url="",
        )
        result = client._resolve_token_url(conn)
        assert result == "https://www.arcgis.com/sharing/rest/generateToken"


# =============================================================================
# _resolve_token_url — enterprise fallback
# =============================================================================

class TestResolveTokenUrlEnterprise:
    """Test _resolve_token_url() fallback for arcgis_enterprise."""

    def test_enterprise_with_portal_in_url(self):
        http = MagicMock()
        http.get.return_value = _mock_response({})  # discovery fails
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            server_url="https://gis.company.com/portal/rest/services",
            server_type="arcgis_enterprise",
            token_url="",
        )
        result = client._resolve_token_url(conn)
        assert result == "https://gis.company.com/portal/sharing/rest/generateToken"

    def test_enterprise_without_portal_in_url(self):
        http = MagicMock()
        http.get.return_value = _mock_response({})
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            server_url="https://gis.company.com/arcgis/rest/services",
            server_type="arcgis_enterprise",
            token_url="",
        )
        result = client._resolve_token_url(conn)
        assert result == "https://gis.company.com/arcgis/rest/services/sharing/rest/generateToken"


# =============================================================================
# _resolve_token_url — server fallback
# =============================================================================

class TestResolveTokenUrlServerFallback:
    """Test _resolve_token_url() fallback for arcgis_server."""

    def test_server_with_rest_services(self):
        http = MagicMock()
        http.get.return_value = _mock_response({})
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            server_url="https://gis.company.com/arcgis/rest/services",
            server_type="arcgis_server",
            token_url="",
        )
        result = client._resolve_token_url(conn)
        assert result == "https://gis.company.com/arcgis/tokens/generateToken"

    def test_server_with_arcgis_in_url_no_rest_services(self):
        http = MagicMock()
        http.get.return_value = _mock_response({})
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            server_url="https://gis.company.com/arcgis",
            server_type="arcgis_server",
            token_url="",
        )
        result = client._resolve_token_url(conn)
        assert result == "https://gis.company.com/arcgis/tokens/generateToken"

    def test_server_plain_url(self):
        http = MagicMock()
        http.get.return_value = _mock_response({})
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            server_url="https://gis.company.com",
            server_type="arcgis_server",
            token_url="",
        )
        result = client._resolve_token_url(conn)
        assert result == "https://gis.company.com/tokens/generateToken"


# =============================================================================
# _resolve_token_url — auto-discovery success
# =============================================================================

class TestResolveTokenUrlDiscovery:
    """Test _resolve_token_url() when auto-discovery succeeds."""

    def test_discovery_returns_token_url(self):
        http = MagicMock()
        http.get.return_value = _mock_response({
            "authInfo": {
                "tokenServicesUrl": "https://gis.company.com/portal/sharing/rest/generateToken"
            }
        })
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            server_url="https://gis.company.com/arcgis/rest/services",
            server_type="arcgis_server",
            token_url="",
        )
        result = client._resolve_token_url(conn)
        assert result == "https://gis.company.com/portal/sharing/rest/generateToken"

    def test_discovery_takes_priority_over_fallback(self):
        http = MagicMock()
        http.get.return_value = _mock_response({
            "authInfo": {
                "tokenServicesUrl": "https://discovered.example.com/token"
            }
        })
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            server_url="https://gis.company.com/arcgis/rest/services",
            server_type="arcgis_enterprise",
            token_url="",
        )
        result = client._resolve_token_url(conn)
        assert result == "https://discovered.example.com/token"


# =============================================================================
# _discover_token_url
# =============================================================================

class TestDiscoverTokenUrl:
    """Tests for _discover_token_url()."""

    def test_rest_services_in_url_tries_rest_info(self):
        http = MagicMock()
        http.get.return_value = _mock_response({
            "authInfo": {
                "tokenServicesUrl": "https://gis.example.com/arcgis/tokens/generateToken"
            }
        })
        client = ArcGISClient(http_client=http)

        result = client._discover_token_url(
            "https://gis.example.com/arcgis/rest/services"
        )
        assert result == "https://gis.example.com/arcgis/tokens/generateToken"
        # Should have called GET with /rest/info
        call_url = http.get.call_args_list[0][0][0]
        assert "/rest/info" in call_url

    def test_no_auth_info_returns_none(self):
        http = MagicMock()
        http.get.return_value = _mock_response({"currentVersion": 10.91})
        client = ArcGISClient(http_client=http)

        result = client._discover_token_url("https://gis.example.com/arcgis/rest/services")
        assert result is None

    def test_empty_token_url_returns_none(self):
        http = MagicMock()
        http.get.return_value = _mock_response({
            "authInfo": {"tokenServicesUrl": ""}
        })
        client = ArcGISClient(http_client=http)

        result = client._discover_token_url("https://gis.example.com/arcgis/rest/services")
        assert result is None

    def test_network_error_returns_none(self):
        http = MagicMock()
        http.get.side_effect = Exception("connection refused")
        client = ArcGISClient(http_client=http)

        result = client._discover_token_url("https://gis.example.com/arcgis/rest/services")
        assert result is None

    def test_multiple_candidates_tried(self):
        """If first candidate fails, second should be tried."""
        call_count = 0

        def mock_get(url, params=None, timeout=10):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("fail")
            return _mock_response({
                "authInfo": {"tokenServicesUrl": "https://found.example.com/token"}
            })

        http = MagicMock()
        http.get.side_effect = mock_get
        client = ArcGISClient(http_client=http)

        result = client._discover_token_url(
            "https://gis.example.com/arcgis/rest/services"
        )
        assert result == "https://found.example.com/token"
        assert call_count >= 2

    def test_deduplication_of_candidates(self):
        """URLs like /rest/services should produce /rest/info only once (dedup)."""
        http = MagicMock()
        http.get.return_value = _mock_response({})
        client = ArcGISClient(http_client=http)

        client._discover_token_url("https://gis.example.com/arcgis/rest/services")
        # Verify no duplicate URLs were queried
        called_urls = [call[0][0] for call in http.get.call_args_list]
        assert len(called_urls) == len(set(called_urls))

    def test_plain_url_appends_rest_info(self):
        http = MagicMock()
        http.get.return_value = _mock_response({
            "authInfo": {"tokenServicesUrl": "https://gis.example.com/token"}
        })
        client = ArcGISClient(http_client=http)

        result = client._discover_token_url("https://gis.example.com")
        assert result == "https://gis.example.com/token"
        call_url = http.get.call_args_list[0][0][0]
        assert call_url == "https://gis.example.com/rest/info"

    def test_trailing_slash_stripped(self):
        http = MagicMock()
        http.get.return_value = _mock_response({
            "authInfo": {"tokenServicesUrl": "https://x.com/tok"}
        })
        client = ArcGISClient(http_client=http)

        result = client._discover_token_url("https://gis.example.com/arcgis/")
        assert result == "https://x.com/tok"

    def test_passes_f_json_param(self):
        http = MagicMock()
        http.get.return_value = _mock_response({})
        client = ArcGISClient(http_client=http)

        client._discover_token_url("https://gis.example.com")
        for call in http.get.call_args_list:
            assert call[1].get("params") == {"f": "json"} or \
                   (len(call[0]) > 1 and call[0][1] == {"f": "json"})
