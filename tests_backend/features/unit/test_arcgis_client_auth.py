"""
Unit tests for ArcGISClient authentication methods.
"""

import time
import pytest
from unittest.mock import MagicMock, patch

from app.core.features.arcgis_client import ArcGISClient, ArcGISConnectionInfo

pytestmark = pytest.mark.unit


# =============================================================================
# Helpers
# =============================================================================

def _make_conn(**overrides):
    """Create a default ArcGISConnectionInfo with sensible defaults."""
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
# authenticate — public
# =============================================================================

class TestAuthenticatePublic:
    """Test authenticate() with public auth_type."""

    def test_public_returns_none(self):
        http = MagicMock()
        client = ArcGISClient(http_client=http)
        conn = _make_conn(auth_type="public")
        assert client.authenticate(conn) is None
        http.post.assert_not_called()
        http.get.assert_not_called()


# =============================================================================
# authenticate — token caching
# =============================================================================

class TestAuthenticateTokenCaching:
    """Test token caching and expiry in authenticate()."""

    def test_cached_token_returned_when_not_expired(self):
        http = MagicMock()
        client = ArcGISClient(http_client=http)
        conn = _make_conn(auth_type="token")

        # Seed the cache
        cache_key = f"{conn.server_url}:{conn.username}"
        client._token_cache[cache_key] = {
            "token": "cached-token-abc",
            "expires": time.time() + 3600,
        }

        result = client.authenticate(conn)
        assert result == "cached-token-abc"
        http.post.assert_not_called()

    def test_expired_cache_triggers_new_auth(self):
        http = MagicMock()
        # Provide a successful token response for the generateToken call
        http.post.return_value = _mock_response({"token": "new-token", "expires": int(time.time() + 3600)})
        # Discovery GET should fail or return no auth info
        http.get.return_value = _mock_response({})

        client = ArcGISClient(http_client=http)
        conn = _make_conn(auth_type="token", token_url="https://myserver.example.com/tokens/generateToken")

        cache_key = f"{conn.server_url}:{conn.username}"
        client._token_cache[cache_key] = {
            "token": "old-token",
            "expires": time.time() - 100,  # expired
        }

        result = client.authenticate(conn)
        assert result == "new-token"
        assert http.post.called

    def test_cache_key_uses_client_id_for_oauth2(self):
        http = MagicMock()
        http.post.return_value = _mock_response({
            "access_token": "oauth-tok", "expires_in": 7200
        })
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            auth_type="oauth2",
            client_id="my_client_id",
            client_secret="my_secret",
            server_url="https://org.maps.arcgis.com",
            username="",
        )

        result = client.authenticate(conn)
        assert result == "oauth-tok"

        cache_key = f"{conn.server_url}:{conn.client_id}"
        assert cache_key in client._token_cache


# =============================================================================
# _authenticate_oauth2
# =============================================================================

class TestAuthenticateOAuth2:
    """Test _authenticate_oauth2()."""

    def test_success_returns_token(self):
        http = MagicMock()
        http.post.return_value = _mock_response({
            "access_token": "tok123",
            "expires_in": 3600,
        })
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            auth_type="oauth2",
            client_id="cid",
            client_secret="csec",
            server_url="https://org.maps.arcgis.com",
        )

        result = client._authenticate_oauth2(conn, "cache_key")
        assert result == "tok123"
        assert client._token_cache["cache_key"]["token"] == "tok123"

    def test_success_caches_with_margin(self):
        http = MagicMock()
        http.post.return_value = _mock_response({
            "access_token": "tok",
            "expires_in": 7200,
        })
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            auth_type="oauth2",
            client_id="cid",
            client_secret="csec",
            server_url="https://org.maps.arcgis.com",
        )

        before = time.time()
        client._authenticate_oauth2(conn, "ck")
        cached = client._token_cache["ck"]
        # Expires should be ~ now + 7200 - 60
        assert cached["expires"] >= before + 7200 - 61
        assert cached["expires"] <= time.time() + 7200

    def test_default_expires_in_used_when_missing(self):
        http = MagicMock()
        http.post.return_value = _mock_response({"access_token": "tok"})
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            auth_type="oauth2",
            client_id="cid",
            client_secret="csec",
            server_url="https://org.maps.arcgis.com",
        )

        before = time.time()
        client._authenticate_oauth2(conn, "ck")
        # Default is 7200
        assert client._token_cache["ck"]["expires"] >= before + 7200 - 61

    def test_error_response_raises_connection_error(self):
        http = MagicMock()
        http.post.return_value = _mock_response({
            "error": {"message": "Invalid client_id"}
        })
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            auth_type="oauth2",
            client_id="bad",
            client_secret="bad",
            server_url="https://org.maps.arcgis.com",
        )

        with pytest.raises(ConnectionError, match="OAuth2 fallito"):
            client._authenticate_oauth2(conn, "ck")

    def test_error_description_fallback(self):
        http = MagicMock()
        http.post.return_value = _mock_response({
            "error_description": "something wrong"
        })
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            auth_type="oauth2",
            client_id="x",
            client_secret="x",
            server_url="https://org.maps.arcgis.com",
        )

        with pytest.raises(ConnectionError, match="OAuth2 fallito"):
            client._authenticate_oauth2(conn, "ck")

    def test_network_error_raises_connection_error(self):
        import requests as req
        http = MagicMock()
        http.post.side_effect = req.RequestException("timeout")
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            auth_type="oauth2",
            client_id="x",
            client_secret="x",
            server_url="https://org.maps.arcgis.com",
        )

        with pytest.raises(ConnectionError, match="Errore connessione OAuth2"):
            client._authenticate_oauth2(conn, "ck")

    def test_posts_to_correct_oauth_url(self):
        http = MagicMock()
        http.post.return_value = _mock_response({"access_token": "t", "expires_in": 100})
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            auth_type="oauth2",
            client_id="cid",
            client_secret="csec",
            server_url="https://myorg.maps.arcgis.com/sharing/rest",
        )

        client._authenticate_oauth2(conn, "ck")
        call_url = http.post.call_args[0][0]
        assert call_url == "https://myorg.maps.arcgis.com/sharing/rest/oauth2/token"


# =============================================================================
# _authenticate_token
# =============================================================================

class TestAuthenticateToken:
    """Test _authenticate_token()."""

    def test_success_first_strategy(self):
        http = MagicMock()
        http.post.return_value = _mock_response({
            "token": "tok-abc",
            "expires": int((time.time() + 3600) * 1000),  # ms timestamp
        })
        http.get.return_value = _mock_response({})
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            token_url="https://myserver.example.com/tokens/generateToken"
        )

        result = client._authenticate_token(conn, "ck")
        assert result == "tok-abc"
        assert client._token_cache["ck"]["token"] == "tok-abc"

    def test_token_expires_ms_converted_to_seconds(self):
        """Expires > 1e10 should be treated as milliseconds."""
        future_ms = int((time.time() + 3600) * 1000)
        http = MagicMock()
        http.post.return_value = _mock_response({
            "token": "tok",
            "expires": future_ms,
        })
        http.get.return_value = _mock_response({})
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            token_url="https://myserver.example.com/tokens/generateToken"
        )

        client._authenticate_token(conn, "ck")
        cached_exp = client._token_cache["ck"]["expires"]
        # Should be in seconds range, not ms
        assert cached_exp < 1e10

    def test_token_expires_seconds_kept(self):
        """Expires <= 1e10 should be kept as-is (seconds)."""
        future_sec = int(time.time() + 3600)
        http = MagicMock()
        http.post.return_value = _mock_response({
            "token": "tok",
            "expires": future_sec,
        })
        http.get.return_value = _mock_response({})
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            token_url="https://myserver.example.com/tokens/generateToken"
        )

        client._authenticate_token(conn, "ck")
        cached_exp = client._token_cache["ck"]["expires"]
        assert cached_exp == future_sec - 60

    def test_all_strategies_fail_raises_connection_error(self):
        http = MagicMock()
        http.post.return_value = _mock_response({
            "error": {"message": "Invalid credentials"}
        })
        http.get.return_value = _mock_response({})
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            token_url="https://myserver.example.com/tokens/generateToken"
        )

        with pytest.raises(ConnectionError, match="Autenticazione fallita"):
            client._authenticate_token(conn, "ck")

    def test_network_error_on_all_strategies_raises(self):
        import requests as req
        http = MagicMock()
        http.post.side_effect = req.RequestException("conn refused")
        http.get.return_value = _mock_response({})
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            token_url="https://myserver.example.com/tokens/generateToken"
        )

        with pytest.raises(ConnectionError, match="Autenticazione fallita"):
            client._authenticate_token(conn, "ck")

    def test_online_tries_two_strategies(self):
        """ArcGIS Online should try referer then requestip."""
        call_count = 0

        def mock_post(url, data=None, timeout=30, headers=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_response({"error": {"message": "fail"}})
            return _mock_response({
                "token": "tok-ip",
                "expires": int(time.time() + 3600),
            })

        http = MagicMock()
        http.post.side_effect = mock_post
        http.get.return_value = _mock_response({})
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            server_url="https://services.arcgis.com/xxx",
            server_type="arcgis_online",
            token_url="https://www.arcgis.com/sharing/rest/generateToken",
        )

        result = client._authenticate_token(conn, "ck")
        assert result == "tok-ip"
        assert http.post.call_count == 2

    def test_server_tries_only_referer_strategy(self):
        """Non-online servers should only try referer."""
        http = MagicMock()
        http.post.return_value = _mock_response({
            "error": {"message": "fail"}
        })
        http.get.return_value = _mock_response({})
        client = ArcGISClient(http_client=http)
        conn = _make_conn(
            server_url="https://myserver.example.com/arcgis/rest/services",
            server_type="arcgis_server",
            token_url="https://myserver.example.com/arcgis/tokens/generateToken",
        )

        with pytest.raises(ConnectionError):
            client._authenticate_token(conn, "ck")
        # Only 1 strategy for non-online
        assert http.post.call_count == 1
