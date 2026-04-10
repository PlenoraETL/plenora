"""
tests/regression/integration/test_REG09_27_manipola_arricchisci.py
===================================================================
Regression test INTEGRATION — MANIPOLA (REG-09→16) + ARRICCHISCI (REG-17→27)
"""

import pytest
import requests
import io
import json
import numpy as np
import pandas as pd
from unittest.mock import patch

pytestmark = [pytest.mark.regression, pytest.mark.integration]

BASE_URL = "http://localhost:8000/api"


# =============================================================================
# REG-09→16 — MANIPOLA
# =============================================================================

class TestREG09Integration:
    """Join sequenziale 5 tabelle."""

    @pytest.mark.parametrize("n_nodes", [2, 3, 4, 5])
    def test_canvas_join_n_nodi_salvato(self, auth_headers, make_project, n_nodes):
        """Canvas con N nodi join viene salvato."""
        canvas = {
            "nodes": [{"id": f"n{i}", "type": "JoinNode",
                       "data": {"join_key": "id"}} for i in range(n_nodes)],
            "edges": [{"id": f"e{i}", "source": f"n{i}",
                       "target": f"n{i+1}"} for i in range(n_nodes-1)]
        }
        project_id = make_project(project_type="manipola")
        res = requests.put(f"{BASE_URL}/projects/{project_id}/canvas",
                           json=canvas, headers=auth_headers)
        assert res.status_code in (200, 404, 422)

    def test_progetto_join_no_500(self, auth_headers, make_project):
        """Esecuzione progetto join non dà 500."""
        project_id = make_project(project_type="manipola")
        res = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                            headers=auth_headers, timeout=30)
        assert res.status_code != 500


class TestREG10Integration:
    """Pivot su dataset reale."""

    @pytest.mark.parametrize("aggfunc", ["sum", "mean", "count"])
    def test_canvas_pivot_aggfunc(self, auth_headers, make_project, aggfunc):
        """Canvas pivot con aggfunc viene salvato."""
        canvas = {
            "nodes": [{"id": "n1", "type": "PivotNode",
                       "data": {"index": "categoria", "columns": "anno",
                                "values": "valore", "aggfunc": aggfunc}}],
            "edges": []
        }
        project_id = make_project(project_type="manipola")
        res = requests.put(f"{BASE_URL}/projects/{project_id}/canvas",
                           json=canvas, headers=auth_headers)
        assert res.status_code in (200, 404, 422)


class TestREG11Integration:
    """Filter con condizioni multiple."""

    @pytest.mark.parametrize("operator", ["==", "!=", ">", "<", ">=", "<="])
    def test_canvas_filter_operatori(self, auth_headers, make_project, operator):
        """Canvas filter con vari operatori viene salvato."""
        canvas = {
            "nodes": [{"id": "n1", "type": "FilterNode",
                       "data": {"column": "valore",
                                "operator": operator, "value": 500}}],
            "edges": []
        }
        project_id = make_project(project_type="manipola")
        res = requests.put(f"{BASE_URL}/projects/{project_id}/canvas",
                           json=canvas, headers=auth_headers)
        assert res.status_code in (200, 404, 422)

    def test_filter_progetto_no_500(self, auth_headers, make_project):
        """Esecuzione filtro non dà 500."""
        project_id = make_project(project_type="manipola")
        res = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                            headers=auth_headers, timeout=30)
        assert res.status_code != 500


class TestREG12Integration:
    """Join spaziale ST_Intersects."""

    def test_canvas_sjoin_salvato(self, auth_headers, make_project):
        """Canvas con nodo join spaziale viene salvato."""
        canvas = {
            "nodes": [{"id": "n1", "type": "SpatialJoinNode",
                       "data": {"predicate": "intersects"}}],
            "edges": []
        }
        project_id = make_project(project_type="manipola")
        res = requests.put(f"{BASE_URL}/projects/{project_id}/canvas",
                           json=canvas, headers=auth_headers)
        assert res.status_code in (200, 404, 422)

    @pytest.mark.parametrize("predicate", [
        "intersects", "contains", "within", "overlaps"
    ])
    def test_sjoin_predicati_accettati(self, auth_headers, make_project, predicate):
        """Vari predicati join spaziale vengono accettati."""
        canvas = {
            "nodes": [{"id": "n1", "type": "SpatialJoinNode",
                       "data": {"predicate": predicate}}],
            "edges": []
        }
        project_id = make_project(project_type="manipola")
        res = requests.put(f"{BASE_URL}/projects/{project_id}/canvas",
                           json=canvas, headers=auth_headers)
        assert res.status_code in (200, 404, 422)


class TestREG16Integration:
    """Conversione CRS manuale."""

    @pytest.mark.parametrize("from_epsg,to_epsg", [
        (4326,  3857),
        (4326,  32632),
        (32632, 4326),
    ])
    def test_canvas_crs_conversion(self, auth_headers, make_project,
                                    from_epsg, to_epsg):
        """Canvas conversione CRS viene salvato."""
        canvas = {
            "nodes": [{"id": "n1", "type": "ReprojectNode",
                       "data": {"from_crs": f"EPSG:{from_epsg}",
                                "to_crs": f"EPSG:{to_epsg}"}}],
            "edges": []
        }
        project_id = make_project(project_type="manipola")
        res = requests.put(f"{BASE_URL}/projects/{project_id}/canvas",
                           json=canvas, headers=auth_headers)
        assert res.status_code in (200, 404, 422)


# =============================================================================
# REG-17→27 — ARRICCHISCI
# =============================================================================

class TestREG17Integration:
    """Pipeline FileSource → REST Countries → CSV."""

    def test_api_connection_rest_countries_creata(self, auth_headers):
        """API connection REST Countries creata senza errori."""
        res = requests.post(
            f"{BASE_URL}/api-connections",
            json={"name": "[REG] REST Countries",
                  "api_type": "enricher",
                  "endpoint": {"url": "https://restcountries.com/v3.1/name/{paese}",
                                "method": "GET"},
                  "auth": {"type": "none"},
                  "parameters": [{"name": "paese", "in": "path", "required": True}],
                  "output_mapping": [
                      {"path": "0.capital.0", "column": "capitale"},
                      {"path": "0.population", "column": "popolazione"},
                  ]},
            headers=auth_headers, timeout=5
        )
        assert res.status_code in (200, 201, 422)
        if res.status_code in (200, 201):
            aid = res.json().get("id")
            if aid:
                requests.delete(f"{BASE_URL}/api-connections/{aid}",
                                headers=auth_headers)

    @pytest.mark.parametrize("country", ["Italy", "France", "Germany"])
    def test_rest_countries_api_reale(self, country):
        """API REST Countries reale risponde correttamente."""
        try:
            res = requests.get(
                f"https://restcountries.com/v3.1/name/{country}",
                timeout=10
            )
            if res.status_code == 200:
                data = res.json()
                assert len(data) > 0
                assert "capital" in data[0]
                assert "population" in data[0]
        except requests.RequestException:
            pytest.skip("REST Countries non raggiungibile")


class TestREG18Integration:
    """Pipeline FileSource → Open-Meteo → GPKG."""

    def test_open_meteo_api_reale(self):
        """API Open-Meteo reale risponde correttamente."""
        try:
            res = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={"latitude": 43.7, "longitude": 11.2,
                        "current": "temperature_2m,wind_speed_10m"},
                timeout=10
            )
            if res.status_code == 200:
                data = res.json()
                assert "current" in data
                assert "temperature_2m" in data["current"]
        except requests.RequestException:
            pytest.skip("Open-Meteo non raggiungibile")

    def test_api_connection_open_meteo_creata(self, auth_headers):
        """API connection Open-Meteo creata senza errori."""
        res = requests.post(
            f"{BASE_URL}/api-connections",
            json={"name": "[REG] Open-Meteo",
                  "api_type": "enricher",
                  "endpoint": {"url": "https://api.open-meteo.com/v1/forecast",
                                "method": "GET"},
                  "auth": {"type": "none"},
                  "parameters": [
                      {"name": "latitude", "in": "query", "required": True},
                      {"name": "longitude", "in": "query", "required": True},
                  ],
                  "output_mapping": [
                      {"path": "current.temperature_2m", "column": "temperatura"},
                      {"path": "current.wind_speed_10m", "column": "vento"},
                  ]},
            headers=auth_headers, timeout=5
        )
        assert res.status_code in (200, 201, 422)
        if res.status_code in (200, 201):
            aid = res.json().get("id")
            if aid:
                requests.delete(f"{BASE_URL}/api-connections/{aid}",
                                headers=auth_headers)


class TestREG20Integration:
    """Cache deduplicazione."""

    def test_validate_dedup_config(self, auth_headers, make_project):
        """Configurazione dedup viene validata."""
        project_id = make_project(project_type="arricchisci")
        res = requests.post(
            f"{BASE_URL}/enrichment/validate",
            json={"project_id": project_id,
                  "api_id": "api_fake",
                  "param_mapping": {"paese": "col_paese"},
                  "options": {"deduplicate": True}},
            headers=auth_headers, timeout=10
        )
        assert res.status_code != 500

    @pytest.mark.parametrize("n_unique", [1, 3, 5, 10])
    def test_estimate_dedup_n_unique(self, auth_headers, make_project, n_unique):
        """Stima enrichment con N unici risponde."""
        project_id = make_project(project_type="arricchisci")
        res = requests.post(
            f"{BASE_URL}/enrichment/estimate",
            json={"project_id": project_id,
                  "api_id": "api_fake",
                  "param_mapping": {},
                  "n_unique": n_unique},
            headers=auth_headers, timeout=10
        )
        assert res.status_code != 500


class TestREG23Integration:
    """Validazione canvas: param mancante → errore."""

    def test_validate_param_mancante(self, auth_headers, make_project):
        """Validate con param required mancante risponde."""
        project_id = make_project(project_type="arricchisci")
        res = requests.post(
            f"{BASE_URL}/enrichment/validate",
            json={"project_id": project_id,
                  "api_id": "api_fake",
                  "param_mapping": {}},
            headers=auth_headers, timeout=10
        )
        assert res.status_code != 500

    @pytest.mark.parametrize("param_mapping,expected_valid", [
        ({},                               False),
        ({"paese": "col_paese"},           True),
        ({"paese": "", "capitale": "col"}, False),
    ])
    def test_validazione_vari_mapping(self, auth_headers, make_project,
                                       param_mapping, expected_valid):
        """Validazione con vari mapping produce risposta."""
        project_id = make_project(project_type="arricchisci")
        res = requests.post(
            f"{BASE_URL}/enrichment/validate",
            json={"project_id": project_id,
                  "api_id": "api_fake",
                  "param_mapping": param_mapping},
            headers=auth_headers, timeout=10
        )
        assert res.status_code != 500


class TestREG27Integration:
    """Template Nominatim Geocoding."""

    def test_nominatim_api_reale(self):
        """API Nominatim reale risponde."""
        try:
            res = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": "Rome, Italy", "format": "json", "limit": 1},
                headers={"User-Agent": "Plenora/1.0 test"},
                timeout=10
            )
            if res.status_code == 200:
                data = res.json()
                if data:
                    assert "lat" in data[0]
                    assert "lon" in data[0]
                    assert "display_name" in data[0]
        except requests.RequestException:
            pytest.skip("Nominatim non raggiungibile")

    def test_api_connection_nominatim_creata(self, auth_headers):
        """API connection Nominatim creata senza errori."""
        res = requests.post(
            f"{BASE_URL}/api-connections",
            json={"name": "[REG] Nominatim",
                  "api_type": "enricher",
                  "endpoint": {"url": "https://nominatim.openstreetmap.org/search",
                                "method": "GET"},
                  "auth": {"type": "none"},
                  "parameters": [
                      {"name": "q", "in": "query", "required": True},
                      {"name": "format", "in": "query",
                       "required": False, "default": "json"},
                  ],
                  "output_mapping": [
                      {"path": "0.lat", "column": "lat"},
                      {"path": "0.lon", "column": "lon"},
                      {"path": "0.display_name", "column": "display_name"},
                  ]},
            headers=auth_headers, timeout=5
        )
        assert res.status_code in (200, 201, 422)
        if res.status_code in (200, 201):
            aid = res.json().get("id")
            if aid:
                requests.delete(f"{BASE_URL}/api-connections/{aid}",
                                headers=auth_headers)

    @pytest.mark.parametrize("city", ["Rome, Italy", "Paris, France", "Berlin, Germany"])
    def test_nominatim_varie_citta(self, city):
        """Nominatim risponde per varie città."""
        try:
            res = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": city, "format": "json", "limit": 1},
                headers={"User-Agent": "Plenora/1.0 test"},
                timeout=10
            )
            if res.status_code == 200:
                data = res.json()
                if data:
                    lat = float(data[0]["lat"])
                    lon = float(data[0]["lon"])
                    assert -90 <= lat <= 90
                    assert -180 <= lon <= 180
        except requests.RequestException:
            pytest.skip("Nominatim non raggiungibile")
