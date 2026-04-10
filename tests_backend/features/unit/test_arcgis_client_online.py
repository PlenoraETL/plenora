"""
Test per ArcGISClient: _list_services_online(), search_services_online().
"""

import pytest
from unittest.mock import MagicMock, patch
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
        server_url="https://myorg.maps.arcgis.com",
        server_type="arcgis_online",
        auth_type="public",
        username="testuser",
        password="",
    )
    defaults.update(kw)
    return ArcGISConnectionInfo(**defaults)


def _client(http=None):
    from app.core.features.arcgis_client import ArcGISClient
    return ArcGISClient(http_client=http or MagicMock())


# =========================================================================
# _list_services_online
# =========================================================================

class TestListServicesOnline:
    """Content API items + folder recursion."""

    def test_returns_feature_services(self):
        http = MagicMock()
        http.get.return_value = _resp({
            "items": [
                {
                    "type": "Feature Service",
                    "title": "Parcels",
                    "name": "parcels_svc",
                    "url": "https://services.arcgis.com/x/arcgis/rest/services/Parcels/FeatureServer",
                    "id": "item1",
                    "owner": "testuser",
                    "numViews": 42,
                    "modified": 1700000000000,
                },
                {
                    "type": "Web Map",
                    "title": "MyMap",
                    "url": "https://something",
                    "id": "item2",
                },
            ],
            "folders": [],
        })

        c = _client(http)
        result = c._list_services_online(_conn())

        assert len(result) == 1
        assert result[0]["name"] == "Parcels"
        assert result[0]["service_type"] == "FeatureServer"
        assert result[0]["item_id"] == "item1"
        assert result[0]["num_views"] == 42

    def test_items_without_url_skipped(self):
        http = MagicMock()
        http.get.return_value = _resp({
            "items": [
                {"type": "Feature Service", "title": "NoURL", "id": "x"},
            ],
            "folders": [],
        })
        c = _client(http)
        result = c._list_services_online(_conn())
        assert len(result) == 0

    def test_folder_recursion(self):
        http = MagicMock()

        root_resp = _resp({
            "items": [
                {"type": "Feature Service", "title": "RootSvc",
                 "url": "https://a/FeatureServer", "id": "r1"},
            ],
            "folders": [
                {"id": "folder_abc", "title": "Utilities"},
            ],
        })
        sub_resp = _resp({
            "items": [
                {"type": "Feature Service", "title": "SubSvc",
                 "url": "https://b/FeatureServer", "id": "s1"},
            ],
            "folders": [],
        })

        http.get.side_effect = [root_resp, sub_resp]

        c = _client(http)
        result = c._list_services_online(_conn())

        assert len(result) == 2
        names = [s["name"] for s in result]
        assert "RootSvc" in names
        assert "SubSvc" in names
        # Sub-folder items get folder label
        sub = [s for s in result if s["name"] == "SubSvc"][0]
        assert sub["folder"] == "Utilities"

    def test_no_folder_recursion_from_subfolder(self):
        """When called with a specific folder, do not recurse further."""
        http = MagicMock()
        http.get.return_value = _resp({
            "items": [
                {"type": "Feature Service", "title": "InFolder",
                 "url": "https://x/FS", "id": "f1"},
            ],
            "folders": [
                {"id": "nested", "title": "Nested"},
            ],
        })

        c = _client(http)
        result = c._list_services_online(_conn(), folder="some_folder")

        # Should NOT recurse into sub-folders when already in a folder
        assert http.get.call_count == 1
        assert len(result) == 1

    def test_no_username_falls_back_to_search(self):
        http = MagicMock()
        # search_services_online will be called; provide search response
        http.get.return_value = _resp({
            "results": [
                {"type": "Feature Service", "title": "OrgSvc",
                 "url": "https://z/FS", "id": "o1", "owner": "admin"},
            ],
            "nextStart": -1,
        })

        c = _client(http)
        # auth_type=public so authenticate() returns None without HTTP calls.
        # username="" triggers the fallback to search_services_online.
        conn = _conn(username="", auth_type="public")

        result = c._list_services_online(conn)
        # Should have called search with scope=org
        assert len(result) == 1
        assert result[0]["name"] == "OrgSvc"

    def test_error_in_content_api_raises(self):
        http = MagicMock()
        http.get.return_value = _resp({
            "error": {"message": "Token invalid", "code": 498},
        })

        c = _client(http)
        with pytest.raises(ConnectionError, match="Token invalid"):
            c._list_services_online(_conn())

    def test_request_exception_raises(self):
        http = MagicMock()
        http.get.side_effect = requests.RequestException("DNS failure")

        c = _client(http)
        with pytest.raises(ConnectionError, match="Errore connessione ArcGIS Online"):
            c._list_services_online(_conn())

    def test_token_included_in_params(self):
        http = MagicMock()

        # authenticate call (token auth)
        http.post.return_value = _resp({"token": "mytoken", "expires": 9999999999999})
        http.get.return_value = _resp({"items": [], "folders": []})

        c = _client(http)
        conn = _conn(auth_type="token", username="u", password="p")
        c._list_services_online(conn)

        get_call = http.get.call_args
        params = get_call.kwargs.get("params") or get_call[1].get("params", {})
        assert params.get("token") == "mytoken"

    def test_empty_items_returns_empty(self):
        http = MagicMock()
        http.get.return_value = _resp({"items": [], "folders": []})

        c = _client(http)
        result = c._list_services_online(_conn())
        assert result == []


# =========================================================================
# search_services_online
# =========================================================================

class TestSearchServicesOnline:
    """Search API with scope, pagination, errors."""

    def test_scope_user(self):
        http = MagicMock()
        http.get.return_value = _resp({
            "results": [
                {"title": "MySvc", "url": "https://x/FS", "id": "1", "owner": "testuser"},
            ],
            "nextStart": -1,
        })

        c = _client(http)
        result = c.search_services_online(_conn(), scope="user")

        assert len(result) == 1
        # Verify query includes owner filter
        get_call = http.get.call_args
        params = get_call.kwargs.get("params") or get_call[1].get("params", {})
        assert "owner:testuser" in params["q"]

    def test_scope_org(self):
        http = MagicMock()
        http.get.return_value = _resp({
            "results": [],
            "nextStart": -1,
        })

        c = _client(http)
        c.search_services_online(_conn(), scope="org")

        get_call = http.get.call_args
        params = get_call.kwargs.get("params") or get_call[1].get("params", {})
        assert "orgid:self" in params["q"]

    def test_scope_all(self):
        http = MagicMock()
        http.get.return_value = _resp({
            "results": [],
            "nextStart": -1,
        })

        c = _client(http)
        c.search_services_online(_conn(), scope="all")

        get_call = http.get.call_args
        params = get_call.kwargs.get("params") or get_call[1].get("params", {})
        q = params["q"]
        assert "owner:" not in q
        assert "orgid:" not in q

    def test_custom_query_appended(self):
        http = MagicMock()
        http.get.return_value = _resp({"results": [], "nextStart": -1})

        c = _client(http)
        c.search_services_online(_conn(), query="water", scope="all")

        get_call = http.get.call_args
        params = get_call.kwargs.get("params") or get_call[1].get("params", {})
        assert "water" in params["q"]

    def test_pagination_multiple_pages(self):
        http = MagicMock()

        page1 = _resp({
            "results": [
                {"title": f"Svc{i}", "url": f"https://x/{i}/FS", "id": f"{i}"}
                for i in range(100)
            ],
            "nextStart": 101,
        })
        page2 = _resp({
            "results": [
                {"title": "Last", "url": "https://x/last/FS", "id": "last"},
            ],
            "nextStart": -1,
        })
        http.get.side_effect = [page1, page2]

        c = _client(http)
        result = c.search_services_online(_conn(), scope="all")

        assert len(result) == 101
        assert http.get.call_count == 2

    def test_pagination_stops_when_next_start_minus_one(self):
        http = MagicMock()
        http.get.return_value = _resp({
            "results": [{"title": "A", "url": "https://x/FS", "id": "a"}],
            "nextStart": -1,
        })

        c = _client(http)
        c.search_services_online(_conn(), scope="all")
        assert http.get.call_count == 1

    def test_pagination_stops_when_next_start_not_advancing(self):
        http = MagicMock()
        http.get.return_value = _resp({
            "results": [{"title": "A", "url": "https://x/FS", "id": "a"}],
            "nextStart": 1,  # same as start -> would loop forever
        })

        c = _client(http)
        result = c.search_services_online(_conn(), scope="all")
        assert http.get.call_count == 1

    def test_error_in_search_raises(self):
        http = MagicMock()
        http.get.return_value = _resp({
            "error": {"message": "Rate limit exceeded", "code": 429},
        })

        c = _client(http)
        with pytest.raises(ConnectionError, match="Rate limit exceeded"):
            c.search_services_online(_conn(), scope="all")

    def test_request_exception_raises(self):
        http = MagicMock()
        http.get.side_effect = requests.RequestException("connection reset")

        c = _client(http)
        with pytest.raises(ConnectionError, match="Errore ricerca ArcGIS Online"):
            c.search_services_online(_conn(), scope="all")

    def test_items_without_url_skipped(self):
        http = MagicMock()
        http.get.return_value = _resp({
            "results": [
                {"title": "NoUrl", "id": "n1"},
                {"title": "HasUrl", "url": "https://x/FS", "id": "h1"},
            ],
            "nextStart": -1,
        })

        c = _client(http)
        result = c.search_services_online(_conn(), scope="all")
        assert len(result) == 1
        assert result[0]["name"] == "HasUrl"

    def test_empty_results_returns_empty(self):
        http = MagicMock()
        http.get.return_value = _resp({"results": [], "nextStart": -1})

        c = _client(http)
        result = c.search_services_online(_conn(), scope="all")
        assert result == []
