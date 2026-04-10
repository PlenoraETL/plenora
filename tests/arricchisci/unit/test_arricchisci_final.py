"""
tests/arricchisci/unit/test_arricchisci_final.py
=================================================
Test finali per portare Arricchisci a 400 test totali.

Copre:
  - ApiConnectionService._extract_available_fields casi aggiuntivi
  - ApiConnectionService._extract_rows_from_response casi aggiuntivi
  - ApiConnectionService._get_execution_order edge cases
  - EnrichmentExecutor._group_by_params edge cases
  - EnrichmentExecutor._make_cache_key stabilità
  - ExecutableResult.to_dict campi
  - list_builtin_executables struttura
"""

import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock

pytestmark = pytest.mark.unit

from app.core.arricchisci.api_connector import ApiConnectionService, _nd
from app.core.arricchisci.enrichment_service import EnrichmentExecutor, EnrichmentOptions
from app.core.arricchisci.executable_runner import (
    ExecutableResult, list_builtin_executables
)


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_svc():
    svc = ApiConnectionService.__new__(ApiConnectionService)
    svc.data_dir = "/tmp"
    svc.results_dir = "/tmp"
    svc.api_connections_dir = "/tmp"
    return svc


def make_executor(param_mapping=None, output_mapping=None, options=None):
    api_config = {
        "name": "TestAPI",
        "endpoint": {"url": "http://example.com", "method": "GET"},
        "auth": {"type": "none"},
        "output_mapping": output_mapping or [],
        "static_parameters": {},
        "batch": {},
    }
    return EnrichmentExecutor(
        api_config=api_config,
        param_mapping=param_mapping or {"country": "paese"},
        options=options or EnrichmentOptions(),
    )


# =============================================================================
# ApiConnectionService._extract_available_fields — casi aggiuntivi
# =============================================================================

class TestExtractAvailableFieldsExtra:

    def test_array_con_prefix(self):
        svc = make_svc()
        data = {"items": [{"id": 1, "name": "test"}]}
        fields = svc._extract_available_fields(data)
        paths = [f["path"] for f in fields]
        assert "items" in paths

    def test_nested_tre_livelli(self):
        svc = make_svc()
        data = {"a": {"b": {"c": "valore"}}}
        fields = svc._extract_available_fields(data, max_depth=3)
        paths = [f["path"] for f in fields]
        assert "a" in paths
        assert "a.b" in paths

    def test_array_primitivi_tutti_espansi(self):
        svc = make_svc()
        data = {"coords": [1.0, 2.0, 3.0, 4.0, 5.0]}
        fields = svc._extract_available_fields(data)
        paths = [f["path"] for f in fields]
        assert "coords[0]" in paths
        assert "coords[4]" in paths

    def test_max_10_primitivi(self):
        """Array con >10 primitivi → max 10 espansi."""
        svc = make_svc()
        data = {"vals": list(range(20))}
        fields = svc._extract_available_fields(data)
        primitive_fields = [f for f in fields if "vals[" in f["path"]]
        assert len(primitive_fields) <= 10

    def test_feature_collection_come_array(self):
        """Array di Feature → passa per _extract_geojson_fields."""
        svc = make_svc()
        data = [{"type": "Feature", "properties": {"name": "Roma"},
                 "geometry": {"type": "Point", "coordinates": [12.4, 41.9]}}]
        fields = svc._extract_available_fields(data)
        assert isinstance(fields, list)

    def test_stringa_scalare_ritorna_vuoto(self):
        """Stringa scalare alla root → nessun campo."""
        svc = make_svc()
        fields = svc._extract_available_fields("stringa_semplice")
        assert fields == []


# =============================================================================
# ApiConnectionService._extract_rows_from_response — casi aggiuntivi
# =============================================================================

class TestExtractRowsFromResponseExtra:

    def test_lista_assoluta_path(self):
        """Path tipo [0].name → una riga con valore dalla lista."""
        svc = make_svc()
        data = [{"name": "Italy"}, {"name": "France"}]
        mapping = [{"path": "[0].name", "column": "primo"}]
        rows = svc._extract_rows_from_response(data, mapping)
        assert len(rows) == 1
        assert rows[0]["primo"] == "Italy"

    def test_geojson_path_features(self):
        """Path features[].properties.name su FeatureCollection."""
        svc = make_svc()
        data = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"name": "Roma"},
                 "geometry": {"type": "Point", "coordinates": [12.4, 41.9]}},
            ]
        }
        mapping = [{"path": "properties.name", "column": "citta"}]
        rows = svc._extract_rows_from_response(data, mapping)
        assert len(rows) >= 1

    def test_wildcard_root_vuota(self):
        """Wildcard [*] su array vuoto → nessuna riga."""
        svc = make_svc()
        data = {"items": []}
        mapping = [{"path": "items[*].id", "column": "id"}]
        rows = svc._extract_rows_from_response(data, mapping)
        assert len(rows) == 0

    def test_output_mapping_colonna_default_path(self):
        """Senza 'column' → usa 'path' come nome colonna."""
        svc = make_svc()
        data = {"capital": "Rome"}
        mapping = [{"path": "capital"}]
        rows = svc._extract_rows_from_response(data, mapping)
        assert "capital" in rows[0]


# =============================================================================
# _get_execution_order — edge cases aggiuntivi
# =============================================================================

class TestGetExecutionOrderExtra:

    def _nodes(self, ids):
        return [{"id": nid} for nid in ids]

    def _edges(self, pairs):
        return [{"source": s, "target": t} for s, t in pairs]

    def test_due_nodi_senza_edge(self):
        svc = make_svc()
        nodes = self._nodes(["a", "b"])
        order = svc._get_execution_order(nodes, [])
        assert set(order) == {"a", "b"}

    def test_ordine_rispettato_catena_lunga(self):
        svc = make_svc()
        nodes = self._nodes(["a", "b", "c", "d", "e"])
        edges = self._edges([("a","b"),("b","c"),("c","d"),("d","e")])
        order = svc._get_execution_order(nodes, edges)
        for i, node in enumerate(["a","b","c","d","e"]):
            assert order.index(node) == i

    def test_edge_source_inesistente_skip(self):
        svc = make_svc()
        nodes = self._nodes(["a", "b"])
        edges = self._edges([("INESISTENTE", "b")])
        order = svc._get_execution_order(nodes, edges)
        assert "a" in order and "b" in order


# =============================================================================
# EnrichmentExecutor._group_by_params — edge cases
# =============================================================================

class TestGroupByParamsExtra:

    def test_df_vuoto(self):
        ex = make_executor()
        df = pd.DataFrame({"paese": []})
        groups = ex._group_by_params(df)
        assert len(groups) == 0

    def test_una_riga_un_gruppo(self):
        ex = make_executor()
        df = pd.DataFrame({"paese": ["Italy"]})
        groups = ex._group_by_params(df)
        assert len(groups) == 1

    def test_valori_null_stesso_gruppo(self):
        """Righe con stesso null → stesso gruppo."""
        ex = make_executor()
        df = pd.DataFrame({"paese": [None, None, "Italy"]})
        groups = ex._group_by_params(df)
        # None e None → stessa cache key
        assert len(groups) <= 2


# =============================================================================
# EnrichmentExecutor._make_cache_key — stabilità
# =============================================================================

class TestMakeCacheKeyStabilita:

    def test_stessa_chiave_con_tipi_diversi(self):
        """Stesso valore, tipi diversi → chiavi diverse."""
        k1 = EnrichmentExecutor._make_cache_key({"id": 1})
        k2 = EnrichmentExecutor._make_cache_key({"id": "1"})
        assert k1 != k2

    def test_chiave_con_valori_none(self):
        k = EnrichmentExecutor._make_cache_key({"country": None})
        assert len(k) == 32

    def test_chiave_con_valori_float(self):
        k1 = EnrichmentExecutor._make_cache_key({"lat": 41.9, "lon": 12.4})
        k2 = EnrichmentExecutor._make_cache_key({"lat": 41.9, "lon": 12.4})
        assert k1 == k2

    def test_chiave_con_molti_params(self):
        params = {f"param_{i}": f"val_{i}" for i in range(10)}
        k = EnrichmentExecutor._make_cache_key(params)
        assert len(k) == 32


# =============================================================================
# ExecutableResult — campi aggiuntivi
# =============================================================================

class TestExecutableResultExtra:

    def test_output_size_default_zero(self):
        r = ExecutableResult(success=True)
        assert r.output_size_bytes == 0

    def test_is_file_result_default_true(self):
        r = ExecutableResult(success=True)
        assert r.is_file_result is True

    def test_metadata_personalizzato(self):
        r = ExecutableResult(success=True, metadata={"rows": 100, "format": "csv"})
        assert r.metadata["rows"] == 100

    def test_to_dict_is_file_result(self):
        r = ExecutableResult(success=True, output_path="/tmp/out.csv")
        d = r.to_dict()
        assert "is_file_result" in d


# =============================================================================
# _nd — casi aggiuntivi
# =============================================================================

class TestNdExtra:

    def test_valore_false_non_fallback(self):
        """False non deve causare fallback al default."""
        node = {"attivo": False}
        assert _nd(node, "attivo") is False

    def test_valore_lista_vuota_non_fallback(self):
        node = {"items": []}
        result = _nd(node, "items")
        assert result == []

    def test_data_nested_priorita(self):
        """Se chiave presente in flat E in data → flat vince."""
        node = {"key": "flat_value", "data": {"key": "data_value"}}
        assert _nd(node, "key") == "flat_value"

    def test_default_personalizzato(self):
        node = {}
        assert _nd(node, "missing", default=42) == 42
        assert _nd(node, "missing", default=[]) == []
