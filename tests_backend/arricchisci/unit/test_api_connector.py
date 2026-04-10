"""
tests/arricchisci/unit/test_api_connector.py
=============================================
Unit test sui metodi puri di ApiConnectionService.

NON richiede backend, API o Celery.

Copre:
  - _nd: lettura campo flat + fallback data dict
  - _mask_credentials: tutti i tipi auth
  - _is_geometry: GeoJSON types, non-dict, non-geometry
  - _get_field_type: tutti i tipi Python → tipo JSON
  - _get_sample_value: primitivi, list, dict, geometry, stringa lunga
  - _extract_available_fields: oggetto, array, nested, GeoJSON, max_depth
  - _extract_rows_from_response: oggetto, array, FeatureCollection, wildcard, auto-detect data
  - _extract_json_value: dot notation, array index, nested, inesistente
  - _get_execution_order: lineare, fork, ciclo parziale, nodi disconnessi
"""

import pytest
pytestmark = pytest.mark.unit

from app.core.arricchisci.api_connector import ApiConnectionService, _nd


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_svc():
    from unittest.mock import patch
    with patch("app.core.arricchisci.api_connector.settings") as mock_settings:
        mock_settings.DATA_DIR = "/tmp"
        mock_settings.RESULTS_DIR = "/tmp"
        mock_settings.API_CONNECTIONS_DIR = "/tmp"
        svc = ApiConnectionService.__new__(ApiConnectionService)
        svc.data_dir = "/tmp"
        svc.results_dir = "/tmp"
        svc.api_connections_dir = "/tmp"
    return svc


@pytest.fixture
def svc():
    return make_svc()


# =============================================================================
# _nd — lettura campo nodo
# =============================================================================

class TestNd:

    def test_campo_flat(self):
        node = {"api_id": "abc123", "type": "api_enrich"}
        assert _nd(node, "api_id") == "abc123"

    def test_fallback_data_dict(self):
        node = {"data": {"api_id": "abc123"}}
        assert _nd(node, "api_id") == "abc123"

    def test_flat_vince_su_data(self):
        """Mutation target: campo flat ha priorità su data."""
        node = {"api_id": "flat_value", "data": {"api_id": "data_value"}}
        assert _nd(node, "api_id") == "flat_value"

    def test_default_se_mancante(self):
        node = {"type": "api_enrich"}
        assert _nd(node, "api_id") is None
        assert _nd(node, "api_id", "default_val") == "default_val"

    def test_data_non_dict_ignorato(self):
        node = {"data": "stringa_non_dict"}
        assert _nd(node, "api_id") is None

    def test_valore_zero_non_fallback(self):
        """Mutation target: valore 0 non deve cadere nel fallback."""
        node = {"timeout": 0}
        assert _nd(node, "timeout") == 0


# =============================================================================
# _mask_credentials
# =============================================================================

class TestMaskCredentials:

    def test_mask_bearer_token(self, svc):
        config = {"auth": {"type": "bearer", "token": "secret123"}}
        result = svc._mask_credentials(config)
        assert result["auth"]["token"] == "***"

    def test_mask_password(self, svc):
        config = {"auth": {"type": "basic", "username": "user", "password": "pass123"}}
        result = svc._mask_credentials(config)
        assert result["auth"]["password"] == "***"
        assert result["auth"]["username"] == "user"  # username non mascherato

    def test_mask_client_secret(self, svc):
        config = {"auth": {"type": "oauth2", "client_id": "id", "client_secret": "secret"}}
        result = svc._mask_credentials(config)
        assert result["auth"]["client_secret"] == "***"
        assert result["auth"]["client_id"] == "id"

    def test_mask_key_value(self, svc):
        config = {"auth": {"type": "api_key", "key_name": "X-Key", "key_value": "mykey"}}
        result = svc._mask_credentials(config)
        assert result["auth"]["key_value"] == "***"
        assert result["auth"]["key_name"] == "X-Key"

    def test_no_auth_invariato(self, svc):
        config = {"name": "test", "url": "http://example.com"}
        result = svc._mask_credentials(config)
        assert result == config

    def test_non_modifica_originale(self, svc):
        """Mutation target: deep copy — originale invariato."""
        config = {"auth": {"token": "secret"}}
        svc._mask_credentials(config)
        assert config["auth"]["token"] == "secret"


# =============================================================================
# _is_geometry
# =============================================================================

class TestIsGeometry:

    def test_point(self, svc):
        assert svc._is_geometry({"type": "Point", "coordinates": [12.4, 41.9]}) is True

    def test_polygon(self, svc):
        assert svc._is_geometry({"type": "Polygon", "coordinates": []}) is True

    def test_tutti_i_tipi_geojson(self, svc):
        for t in ["Point", "LineString", "Polygon", "MultiPoint",
                  "MultiLineString", "MultiPolygon", "GeometryCollection"]:
            assert svc._is_geometry({"type": t}) is True

    def test_non_geometry_dict(self, svc):
        assert svc._is_geometry({"type": "Feature", "properties": {}}) is False

    def test_non_dict(self, svc):
        assert svc._is_geometry("POINT(1 2)") is False
        assert svc._is_geometry([1, 2]) is False
        assert svc._is_geometry(None) is False

    def test_dict_senza_type(self, svc):
        assert svc._is_geometry({"coordinates": [1, 2]}) is False


# =============================================================================
# _get_field_type
# =============================================================================

class TestGetFieldType:

    def test_none(self, svc):
        assert svc._get_field_type(None) == "null"

    def test_bool(self, svc):
        """Mutation target: bool prima di int."""
        assert svc._get_field_type(True) == "boolean"
        assert svc._get_field_type(False) == "boolean"

    def test_int(self, svc):
        assert svc._get_field_type(42) == "integer"

    def test_float(self, svc):
        assert svc._get_field_type(3.14) == "number"

    def test_string(self, svc):
        assert svc._get_field_type("hello") == "string"

    def test_list(self, svc):
        assert svc._get_field_type([1, 2, 3]) == "array"

    def test_dict_object(self, svc):
        assert svc._get_field_type({"key": "val"}) == "object"

    def test_dict_geojson(self, svc):
        assert svc._get_field_type({"type": "Point", "coordinates": [1, 2]}) == "geojson"


# =============================================================================
# _get_sample_value
# =============================================================================

class TestGetSampleValue:

    def test_none(self, svc):
        assert svc._get_sample_value(None) == "null"

    def test_stringa_corta(self, svc):
        assert svc._get_sample_value("Roma") == "Roma"

    def test_stringa_lunga_troncata(self, svc):
        val = "x" * 100
        result = svc._get_sample_value(val)
        assert len(result) <= 53  # 50 + "..."
        assert result.endswith("...")

    def test_lista(self, svc):
        result = svc._get_sample_value([1, 2, 3])
        assert "3 items" in result

    def test_dict_normale(self, svc):
        result = svc._get_sample_value({"a": 1})
        assert result == "{...}"

    def test_dict_geometry(self, svc):
        result = svc._get_sample_value({"type": "Point", "coordinates": [1, 2]})
        assert result == "Point"

    def test_int(self, svc):
        assert svc._get_sample_value(42) == "42"

    def test_max_len_custom(self, svc):
        val = "x" * 20
        result = svc._get_sample_value(val, max_len=10)
        assert len(result) <= 13
        assert result.endswith("...")


# =============================================================================
# _extract_available_fields
# =============================================================================

class TestExtractAvailableFields:

    def test_oggetto_semplice(self, svc):
        data = {"name": "Italy", "capital": "Rome", "population": 60000000}
        fields = svc._extract_available_fields(data)
        paths = [f["path"] for f in fields]
        assert "name" in paths
        assert "capital" in paths
        assert "population" in paths

    def test_tipo_corretto(self, svc):
        data = {"name": "Italy", "pop": 60000000, "active": True}
        fields = svc._extract_available_fields(data)
        by_path = {f["path"]: f for f in fields}
        assert by_path["name"]["type"] == "string"
        assert by_path["pop"]["type"] == "integer"
        assert by_path["active"]["type"] == "boolean"

    def test_oggetto_nested(self, svc):
        data = {"country": {"name": "Italy", "code": "IT"}}
        fields = svc._extract_available_fields(data)
        paths = [f["path"] for f in fields]
        assert "country" in paths
        assert "country.name" in paths
        assert "country.code" in paths

    def test_array_di_oggetti(self, svc):
        data = [{"id": 1, "nome": "Roma"}, {"id": 2, "nome": "Milano"}]
        fields = svc._extract_available_fields(data)
        paths = [f["path"] for f in fields]
        assert "id" in paths
        assert "nome" in paths

    def test_max_depth_zero(self, svc):
        data = {"a": {"b": {"c": "deep"}}}
        fields = svc._extract_available_fields(data, max_depth=0)
        assert fields == []

    def test_geojson_feature_collection(self, svc):
        data = {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"name": "Roma"},
                          "geometry": {"type": "Point", "coordinates": [12.4, 41.9]}}]
        }
        fields = svc._extract_available_fields(data)
        assert len(fields) > 0

    def test_array_primitivi(self, svc):
        """Array di numeri → indici espansi."""
        data = {"latlng": [41.9, 12.4]}
        fields = svc._extract_available_fields(data)
        paths = [f["path"] for f in fields]
        assert "latlng[0]" in paths
        assert "latlng[1]" in paths

    def test_geometry_non_esplorata(self, svc):
        """Geometria GeoJSON non deve essere esplorata ricorsivamente."""
        data = {"name": "Roma", "geom": {"type": "Point", "coordinates": [12.4, 41.9]}}
        fields = svc._extract_available_fields(data)
        paths = [f["path"] for f in fields]
        assert "geom" in paths
        assert "geom.type" not in paths
        assert "geom.coordinates" not in paths


# =============================================================================
# _extract_rows_from_response
# =============================================================================

class TestExtractRowsFromResponse:

    def test_oggetto_singolo(self, svc):
        data = {"capital": "Rome", "population": 60000000}
        mapping = [
            {"path": "capital",    "column": "capitale"},
            {"path": "population", "column": "pop"},
        ]
        rows = svc._extract_rows_from_response(data, mapping)
        assert len(rows) == 1
        assert rows[0]["capitale"] == "Rome"
        assert rows[0]["pop"] == 60000000

    def test_array_di_oggetti(self, svc):
        data = [{"name": "Italy"}, {"name": "France"}]
        mapping = [{"path": "name", "column": "paese"}]
        rows = svc._extract_rows_from_response(data, mapping)
        assert len(rows) == 2
        assert rows[0]["paese"] == "Italy"
        assert rows[1]["paese"] == "France"

    def test_geojson_feature_collection(self, svc):
        data = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"name": "Roma"},
                 "geometry": {"type": "Point", "coordinates": [12.4, 41.9]}},
                {"type": "Feature", "properties": {"name": "Milano"},
                 "geometry": {"type": "Point", "coordinates": [9.2, 45.5]}},
            ]
        }
        mapping = [{"path": "properties.name", "column": "citta"}]
        rows = svc._extract_rows_from_response(data, mapping)
        assert len(rows) == 2

    def test_wildcard_path(self, svc):
        """Mutation target: path con [*] → itera sull'array."""
        data = {"data": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]}
        mapping = [
            {"path": "data[*].id",   "column": "id"},
            {"path": "data[*].name", "column": "nome"},
        ]
        rows = svc._extract_rows_from_response(data, mapping)
        assert len(rows) == 2
        assert rows[0]["id"] == 1
        assert rows[1]["nome"] == "B"

    def test_auto_detect_data_array(self, svc):
        """Oggetto con chiave 'data' lista → auto-detect."""
        data = {"meta": {"total": 2}, "data": [{"val": 10}, {"val": 20}]}
        mapping = [{"path": "val", "column": "valore"}]
        rows = svc._extract_rows_from_response(data, mapping)
        assert len(rows) == 2
        assert rows[0]["valore"] == 10

    def test_output_mapping_vuoto(self, svc):
        data = {"a": 1}
        rows = svc._extract_rows_from_response(data, [])
        assert rows == [{}]

    def test_path_inesistente_none(self, svc):
        data = {"name": "Italy"}
        mapping = [{"path": "CAMPO_INESISTENTE", "column": "x"}]
        rows = svc._extract_rows_from_response(data, mapping)
        assert rows[0]["x"] is None


# =============================================================================
# _extract_json_value (dot notation)
# =============================================================================

class TestExtractJsonValue:

    def test_campo_semplice(self, svc):
        data = {"name": "Italy", "capital": "Rome"}
        assert svc._extract_json_value(data, "capital") == "Rome"

    def test_nested_dot(self, svc):
        data = {"country": {"name": "Italy", "code": "IT"}}
        assert svc._extract_json_value(data, "country.name") == "Italy"
        assert svc._extract_json_value(data, "country.code") == "IT"

    def test_array_index(self, svc):
        data = {"coords": [41.9, 12.4]}
        assert svc._extract_json_value(data, "coords[0]") == 41.9
        assert svc._extract_json_value(data, "coords[1]") == 12.4

    def test_path_inesistente_none(self, svc):
        data = {"name": "Italy"}
        assert svc._extract_json_value(data, "CAMPO_XYZ") is None

    def test_path_vuoto(self, svc):
        data = {"name": "Italy"}
        result = svc._extract_json_value(data, "")
        assert result == data or result is None

    def test_deeply_nested(self, svc):
        data = {"a": {"b": {"c": {"d": "valore"}}}}
        assert svc._extract_json_value(data, "a.b.c.d") == "valore"


# =============================================================================
# _get_execution_order
# =============================================================================

class TestGetExecutionOrder:

    def _nodes(self, ids):
        return [{"id": nid} for nid in ids]

    def _edges(self, pairs):
        return [{"source": s, "target": t} for s, t in pairs]

    def test_pipeline_lineare(self, svc):
        nodes = self._nodes(["src", "enrich", "result"])
        edges = self._edges([("src", "enrich"), ("enrich", "result")])
        order = svc._get_execution_order(nodes, edges)
        assert order.index("src") < order.index("enrich")
        assert order.index("enrich") < order.index("result")

    def test_tutti_i_nodi_inclusi(self, svc):
        nodes = self._nodes(["a", "b", "c"])
        edges = self._edges([("a", "b"), ("b", "c")])
        order = svc._get_execution_order(nodes, edges)
        assert len(order) == 3
        assert set(order) == {"a", "b", "c"}

    def test_nodi_disconnessi(self, svc):
        nodes = self._nodes(["a", "b", "c"])
        order = svc._get_execution_order(nodes, [])
        assert set(order) == {"a", "b", "c"}

    def test_fork(self, svc):
        """a → b, a → c."""
        nodes = self._nodes(["a", "b", "c"])
        edges = self._edges([("a", "b"), ("a", "c")])
        order = svc._get_execution_order(nodes, edges)
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")

    def test_nodo_singolo(self, svc):
        nodes = self._nodes(["solo"])
        order = svc._get_execution_order(nodes, [])
        assert order == ["solo"]

    def test_edge_con_nodo_inesistente_ignorato(self, svc):
        """Edge che punta a nodo non esistente → ignorato."""
        nodes = self._nodes(["a", "b"])
        edges = self._edges([("a", "b"), ("b", "INESISTENTE")])
        order = svc._get_execution_order(nodes, edges)
        assert set(order) == {"a", "b"}
