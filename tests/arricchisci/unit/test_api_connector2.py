"""
tests/arricchisci/unit/test_api_connector2.py
=============================================
Unit test aggiuntivi per metodi non coperti in api_connector.py
e enrichment_service.py.

Copre:
  - ApiConnectionService._extract_geojson_fields
  - EnrichmentExecutor._auto_flatten: GeoJSON, lista dict, dict, primitivo
  - EnrichmentExecutor._detect_geometry_type: tutti i tipi GeoJSON
  - EnrichmentExecutor._apply_result: scrittura colonne su DataFrame
  - EnrichmentExecutor._flatten_dict: edge cases
"""

import pytest
import pandas as pd
import json

pytestmark = pytest.mark.unit

from app.core.arricchisci.api_connector import ApiConnectionService
from app.core.arricchisci.enrichment_service import EnrichmentExecutor, EnrichmentOptions


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_svc():
    svc = ApiConnectionService.__new__(ApiConnectionService)
    svc.data_dir = "/tmp"
    svc.results_dir = "/tmp"
    svc.api_connections_dir = "/tmp"
    return svc


def make_executor():
    api_config = {
        "name": "TestAPI",
        "endpoint": {"url": "http://example.com", "method": "GET"},
        "auth": {"type": "none"},
        "output_mapping": [],
        "static_parameters": {},
    }
    return EnrichmentExecutor(
        api_config=api_config,
        param_mapping={},
        options=EnrichmentOptions(),
    )


# =============================================================================
# ApiConnectionService._extract_geojson_fields
# =============================================================================

class TestExtractGeojsonFields:

    def test_feature_collection_properties(self):
        svc = make_svc()
        data = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"name": "Roma", "pop": 4000000},
                "geometry": {"type": "Point", "coordinates": [12.4, 41.9]}
            }]
        }
        fields = svc._extract_geojson_fields(data)
        paths = [f["path"] for f in fields]
        assert any("properties.name" in p for p in paths)
        assert any("properties.pop" in p for p in paths)

    def test_geometry_inclusa(self):
        svc = make_svc()
        data = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"name": "Roma"},
                "geometry": {"type": "Point", "coordinates": [12.4, 41.9]}
            }]
        }
        fields = svc._extract_geojson_fields(data)
        paths = [f["path"] for f in fields]
        assert any("geometry" in p for p in paths)

    def test_id_incluso(self):
        svc = make_svc()
        data = {
            "type": "FeatureCollection",
            "features": [{
                "id": 42,
                "type": "Feature",
                "properties": {"name": "Roma"},
                "geometry": None
            }]
        }
        fields = svc._extract_geojson_fields(data)
        paths = [f["path"] for f in fields]
        assert any("id" in p for p in paths)

    def test_features_vuote_lista_vuota(self):
        svc = make_svc()
        data = {"type": "FeatureCollection", "features": []}
        fields = svc._extract_geojson_fields(data)
        assert fields == []

    def test_input_lista_diretta(self):
        """data è direttamente una lista di features."""
        svc = make_svc()
        features = [{
            "type": "Feature",
            "properties": {"nome": "test"},
            "geometry": None
        }]
        fields = svc._extract_geojson_fields(features)
        assert isinstance(fields, list)

    def test_feature_non_dict_skip(self):
        svc = make_svc()
        data = {
            "type": "FeatureCollection",
            "features": ["stringa_non_dict"]
        }
        fields = svc._extract_geojson_fields(data)
        assert fields == []


# =============================================================================
# EnrichmentExecutor._auto_flatten
# =============================================================================

class TestAutoFlatten:

    def test_geojson_feature_collection(self):
        ex = make_executor()
        data = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature",
                 "properties": {"name": "Roma", "pop": 4000000},
                 "geometry": {"type": "Point", "coordinates": [12.4, 41.9]}},
                {"type": "Feature",
                 "properties": {"name": "Milano", "pop": 3000000},
                 "geometry": {"type": "Point", "coordinates": [9.2, 45.5]}},
            ]
        }
        rows = ex._auto_flatten(data)
        assert len(rows) == 2
        assert rows[0]["name"] == "Roma"
        assert "geometry" in rows[0]

    def test_geojson_geometry_a_wkt(self):
        """Geometria GeoJSON → convertita in WKT."""
        ex = make_executor()
        data = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Point", "coordinates": [12.4, 41.9]}
            }]
        }
        rows = ex._auto_flatten(data)
        assert "geometry" in rows[0]
        assert "POINT" in str(rows[0]["geometry"]).upper()

    def test_lista_di_dict(self):
        ex = make_executor()
        data = [{"id": 1, "name": "Italy"}, {"id": 2, "name": "France"}]
        rows = ex._auto_flatten(data)
        assert len(rows) == 2
        assert rows[0]["name"] == "Italy"

    def test_lista_di_primitivi(self):
        """Lista non-dict → una riga con _response."""
        ex = make_executor()
        data = [1, 2, 3]
        rows = ex._auto_flatten(data)
        assert len(rows) == 1
        assert "_response" in rows[0]

    def test_dict_generico(self):
        ex = make_executor()
        data = {"capital": "Rome", "population": 60000000}
        rows = ex._auto_flatten(data)
        assert len(rows) == 1
        assert rows[0]["capital"] == "Rome"

    def test_primitivo_scalare(self):
        """Scalare non dict/list → una riga con _response."""
        ex = make_executor()
        rows = ex._auto_flatten("risposta semplice")
        assert len(rows) == 1
        assert rows[0]["_response"] == "risposta semplice"

    def test_float_intero_convertito(self):
        """Float che è intero (114.0) → int nella riga."""
        ex = make_executor()
        data = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"count": 114.0},
                "geometry": None
            }]
        }
        rows = ex._auto_flatten(data)
        assert rows[0]["count"] == 114
        assert isinstance(rows[0]["count"], int)

    def test_detected_column_types_popolati(self):
        """Dopo auto_flatten i tipi colonna devono essere rilevati."""
        ex = make_executor()
        data = [{"name": "Italy", "pop": 60000000, "active": True}]
        ex._auto_flatten(data)
        assert "name" in ex.detected_column_types
        assert "pop" in ex.detected_column_types


# =============================================================================
# EnrichmentExecutor._detect_geometry_type
# =============================================================================

class TestDetectGeometryType:

    def test_point(self):
        ex = make_executor()
        ex._detect_geometry_type("geom", "point")
        assert ex.detected_column_types["geom"] == "geometry:point"

    def test_multipolygon(self):
        ex = make_executor()
        ex._detect_geometry_type("shape", "multipolygon")
        assert ex.detected_column_types["shape"] == "geometry:multipolygon"

    def test_linestring(self):
        ex = make_executor()
        ex._detect_geometry_type("percorso", "linestring")
        assert ex.detected_column_types["percorso"] == "geometry:line"

    def test_tipo_sconosciuto_fallback(self):
        ex = make_executor()
        ex._detect_geometry_type("geom", "tipoXYZ")
        assert ex.detected_column_types["geom"] == "geometry"

    def test_tutti_i_tipi(self):
        ex = make_executor()
        tipi = ["point", "multipoint", "linestring", "multilinestring",
                "polygon", "multipolygon", "geometrycollection"]
        for t in tipi:
            ex._detect_geometry_type(f"geom_{t}", t)
            assert f"geom_{t}" in ex.detected_column_types


# =============================================================================
# EnrichmentExecutor._apply_result
# =============================================================================

class TestApplyResult:

    def test_applica_valori_riga(self):
        ex = make_executor()
        df = pd.DataFrame({"paese": ["Italy", "France"], "capitale": [None, None]})
        ex._apply_result(df, 0, {"capitale": "Rome", "pop": 60000000})
        assert df.at[0, "capitale"] == "Rome"
        assert df.at[0, "pop"] == 60000000
        # Riga 1 non modificata
        assert df.at[1, "capitale"] is None

    def test_crea_colonna_nuova(self):
        ex = make_executor()
        df = pd.DataFrame({"paese": ["Italy"]})
        ex._apply_result(df, 0, {"nuova_colonna": "valore"})
        assert "nuova_colonna" in df.columns
        assert df.at[0, "nuova_colonna"] == "valore"

    def test_result_vuoto_no_crash(self):
        ex = make_executor()
        df = pd.DataFrame({"paese": ["Italy"]})
        ex._apply_result(df, 0, {})
        assert list(df.columns) == ["paese"]
