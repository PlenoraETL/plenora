"""
Test per ArcGISClient: list_services(), _is_arcgis_online(),
_list_services_server(), _get_rest_services_url().
"""

import pytest
from unittest.mock import MagicMock, patch, call

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helper: mock HTTP + connessione
# ---------------------------------------------------------------------------

def _make_response(json_data, status_code=200):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.status_code = status_code
    resp.text = ""
    return resp


def _make_conn(**overrides):
    from app.core.features.arcgis_client import ArcGISConnectionInfo
    defaults = dict(
        server_url="https://myserver.example.com/arcgis",
        server_type="arcgis_server",
        auth_type="public",
        username="",
        password="",
    )
    defaults.update(overrides)
    return ArcGISConnectionInfo(**defaults)


def _make_client(http=None):
    from app.core.features.arcgis_client import ArcGISClient
    return ArcGISClient(http_client=http or MagicMock())


# =========================================================================
# _is_arcgis_online
# =========================================================================

class TestIsArcgisOnline:
    """Verifica la detection ArcGIS Online vs Server/Enterprise."""

    def test_arcgis_com_url_detected(self):
        conn = _make_conn(
            server_url="https://services.arcgis.com/abc123",
            server_type="arcgis_server",
        )
        client = _make_client()
        assert client._is_arcgis_online(conn) is True

    def test_maps_arcgis_com_detected(self):
        conn = _make_conn(
            server_url="https://myorg.maps.arcgis.com",
            server_type="arcgis_enterprise",
        )
        client = _make_client()
        assert client._is_arcgis_online(conn) is True

    def test_server_type_arcgis_online_detected(self):
        conn = _make_conn(
            server_url="https://custom.example.com",
            server_type="arcgis_online",
        )
        client = _make_client()
        assert client._is_arcgis_online(conn) is True

    def test_plain_server_not_detected(self):
        conn = _make_conn(
            server_url="https://gis.internal.example.com/arcgis",
            server_type="arcgis_server",
        )
        client = _make_client()
        assert client._is_arcgis_online(conn) is False

    def test_enterprise_not_detected(self):
        conn = _make_conn(
            server_url="https://portal.corp.local/portal",
            server_type="arcgis_enterprise",
        )
        client = _make_client()
        assert client._is_arcgis_online(conn) is False

    def test_case_insensitive(self):
        conn = _make_conn(
            server_url="https://SERVICES.ARCGIS.COM/xyz",
            server_type="arcgis_server",
        )
        client = _make_client()
        assert client._is_arcgis_online(conn) is True


# =========================================================================
# _get_rest_services_url
# =========================================================================

class TestGetRestServicesUrl:
    """URL normalization per il catalogo REST."""

    def test_already_ends_with_rest_services(self):
        conn = _make_conn(server_url="https://s.example.com/arcgis/rest/services")
        client = _make_client()
        assert client._get_rest_services_url(conn) == "https://s.example.com/arcgis/rest/services"

    def test_trailing_slash_stripped(self):
        conn = _make_conn(server_url="https://s.example.com/arcgis/rest/services/")
        client = _make_client()
        assert client._get_rest_services_url(conn) == "https://s.example.com/arcgis/rest/services"

    def test_ends_with_rest(self):
        conn = _make_conn(server_url="https://s.example.com/arcgis/rest")
        client = _make_client()
        assert client._get_rest_services_url(conn) == "https://s.example.com/arcgis/rest/services"

    def test_contains_rest_services_with_extra_path(self):
        conn = _make_conn(server_url="https://s.example.com/arcgis/rest/services/MyFolder/MapServer")
        client = _make_client()
        assert client._get_rest_services_url(conn) == "https://s.example.com/arcgis/rest/services"

    def test_bare_server_url(self):
        conn = _make_conn(server_url="https://s.example.com/arcgis")
        client = _make_client()
        assert client._get_rest_services_url(conn) == "https://s.example.com/arcgis/rest/services"

    def test_no_arcgis_path(self):
        conn = _make_conn(server_url="https://services.arcgis.com/abc")
        client = _make_client()
        assert client._get_rest_services_url(conn) == "https://services.arcgis.com/abc/rest/services"


# =========================================================================
# list_services  (dispatch)
# =========================================================================

class TestListServicesDispatch:
    """list_services deve scegliere online vs server."""

    def test_dispatches_to_online_for_arcgis_com(self):
        http = MagicMock()
        client = _make_client(http)
        conn = _make_conn(
            server_url="https://myorg.maps.arcgis.com",
            server_type="arcgis_online",
            auth_type="public",
            username="user1",
        )

        # Content API response (root)
        http.get.return_value = _make_response({
            "items": [
                {"type": "Feature Service", "title": "Svc1",
                 "url": "https://services.arcgis.com/abc/FeatureServer", "id": "id1"},
            ],
            "folders": [],
        })

        result = client.list_services(conn)
        assert len(result) == 1
        assert result[0]["name"] == "Svc1"

    def test_dispatches_to_server_for_enterprise(self):
        http = MagicMock()
        client = _make_client(http)
        conn = _make_conn(
            server_url="https://gis.corp.local/arcgis",
            server_type="arcgis_enterprise",
            auth_type="public",
        )

        http.get.return_value = _make_response({
            "services": [
                {"name": "Buildings", "type": "FeatureServer"},
            ],
            "folders": [],
        })

        result = client.list_services(conn)
        assert len(result) == 1
        assert result[0]["name"] == "Buildings"


# =========================================================================
# _list_services_server
# =========================================================================

class TestListServicesServer:
    """Catalogo REST: navigazione folder, depth limit."""

    def test_returns_feature_servers_only(self):
        http = MagicMock()
        client = _make_client(http)
        conn = _make_conn(auth_type="public")

        http.get.return_value = _make_response({
            "services": [
                {"name": "Roads", "type": "FeatureServer"},
                {"name": "Basemap", "type": "MapServer"},
                {"name": "Imagery", "type": "ImageServer"},
            ],
            "folders": [],
        })

        result = client._list_services_server(conn)
        assert len(result) == 1
        assert result[0]["name"] == "Roads"
        assert result[0]["service_type"] == "FeatureServer"

    def test_folder_recursion(self):
        http = MagicMock()
        client = _make_client(http)
        conn = _make_conn(auth_type="public")

        # Root catalog
        root_resp = _make_response({
            "services": [
                {"name": "Root_Svc", "type": "FeatureServer"},
            ],
            "folders": ["Utilities"],
        })
        # Sub-folder catalog
        sub_resp = _make_response({
            "services": [
                {"name": "Utilities/Water", "type": "FeatureServer"},
            ],
            "folders": [],
        })
        http.get.side_effect = [root_resp, sub_resp]

        result = client._list_services_server(conn)
        assert len(result) == 2
        names = {s["name"] for s in result}
        assert "Root_Svc" in names
        assert "Water" in names  # display_name strips folder prefix

    def test_depth_limit_prevents_infinite_recursion(self):
        http = MagicMock()
        client = _make_client(http)
        conn = _make_conn(auth_type="public")

        # Every catalog returns another folder
        def always_folder(*args, **kwargs):
            return _make_response({
                "services": [],
                "folders": ["deeper"],
            })

        http.get.side_effect = always_folder

        result = client._list_services_server(conn, _max_depth=2)
        # depth 0 -> root, 1 -> deeper, 2 -> deeper/deeper ; depth 3 would be > max_depth
        assert http.get.call_count == 3  # depths 0, 1, 2

    def test_error_in_catalog_raises(self):
        http = MagicMock()
        client = _make_client(http)
        conn = _make_conn(auth_type="public")

        http.get.return_value = _make_response({
            "error": {"message": "Token Required", "code": 499},
        })

        with pytest.raises(ConnectionError, match="Token Required"):
            client._list_services_server(conn)

    def test_request_exception_raises(self):
        import requests
        http = MagicMock()
        client = _make_client(http)
        conn = _make_conn(auth_type="public")

        http.get.side_effect = requests.RequestException("timeout")

        with pytest.raises(ConnectionError, match="Errore connessione"):
            client._list_services_server(conn)

    def test_token_included_when_authenticated(self):
        http = MagicMock()
        client = _make_client(http)
        conn = _make_conn(
            auth_type="token",
            username="admin",
            password="pass",
        )

        # authenticate returns a token
        http.post.return_value = _make_response({"token": "tok123", "expires": 9999999999999})
        http.get.return_value = _make_response({"services": [], "folders": []})

        client._list_services_server(conn)

        # The GET should include the token in params
        get_call = http.get.call_args
        assert get_call.kwargs.get("params", {}).get("token") == "tok123" or \
               get_call[1].get("params", {}).get("token") == "tok123"

    def test_service_url_construction(self):
        http = MagicMock()
        client = _make_client(http)
        conn = _make_conn(
            server_url="https://s.example.com/arcgis",
            auth_type="public",
        )

        http.get.return_value = _make_response({
            "services": [
                {"name": "MyFolder/Parcels", "type": "FeatureServer"},
            ],
            "folders": [],
        })

        result = client._list_services_server(conn)
        assert result[0]["url"] == "https://s.example.com/arcgis/rest/services/MyFolder/Parcels/FeatureServer"
        assert result[0]["full_name"] == "MyFolder/Parcels"
        assert result[0]["name"] == "Parcels"

    def test_folder_path_built_correctly(self):
        http = MagicMock()
        client = _make_client(http)
        conn = _make_conn(auth_type="public")

        # Root → has folder "A"
        root = _make_response({"services": [], "folders": ["A"]})
        # A → has folder "B"
        sub_a = _make_response({"services": [], "folders": ["B"]})
        # A/B → has a service, no more folders
        sub_ab = _make_response({
            "services": [{"name": "A/B/Deep", "type": "FeatureServer"}],
            "folders": [],
        })
        http.get.side_effect = [root, sub_a, sub_ab]

        result = client._list_services_server(conn)
        assert len(result) == 1
        assert result[0]["folder"] == "A/B"
