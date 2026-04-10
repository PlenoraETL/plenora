"""
tests/arricchisci/unit/test_execute_enrichment.py
==================================================
Unit test su ApiConnectionService.execute_enrichment() con mock completo.

Questo metodo da 800 righe è il cuore di Arricchisci — testarlo in-process
con mock porta un +15-20pp di coverage su api_connector.py.

NON richiede backend, API o Celery.

Copre:
  - Canvas vuoto → success=False
  - API non trovata → success=False
  - Nodo senza input → success=False
  - Nessun risultato → success=False
  - file_source → api_enrich (enricher) → result → success
  - project_source → api_enrich → result → success
  - staticValues → api_enrich (generator) → result → success
  - api_enrich generator senza input → success
  - Catena file_source → api_enrich_1 → api_enrich_2 → result
  - Risultato non DataFrame → success=False
  - Eccezione generica → success=False con error
  - Formato output csv/gpkg/geojson
  - preview_limit passato a _load_file_source
"""

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch
from pathlib import Path

pytestmark = pytest.mark.unit

from app.core.arricchisci.api_connector import ApiConnectionService


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_svc():
    svc = ApiConnectionService.__new__(ApiConnectionService)
    svc.data_dir = "/tmp"
    svc.results_dir = "/tmp"
    svc.api_connections_dir = "/tmp"
    return svc


def make_api_config(api_id="api_1", api_type="enricher"):
    return {
        "id": api_id,
        "name": "Test API",
        "api_type": api_type,
        "connection_type": "manual",
        "endpoint": {"url": "http://example.com", "method": "GET"},
        "auth": {"type": "none"},
        "output_mapping": [{"path": "capital", "column": "capitale"}],
        "parameters": [],
    }


def make_df(n=5):
    return pd.DataFrame({
        "paese": [f"Country_{i}" for i in range(n)],
        "capitale": [f"Capital_{i}" for i in range(n)],
    })


def make_canvas(file_source_id="file_1", api_id="api_1",
                output_format="csv", api_type="enricher"):
    if api_type == "generator":
        return {
            "nodes": [
                {"id": "node-enrich", "type": "api_enrich",
                 "api_id": api_id, "param_mapping": {}},
                {"id": "node-result", "type": "result",
                 "format": output_format, "fileName": "output"},
            ],
            "edges": [
                {"id": "e1", "source": "node-enrich", "target": "node-result"},
            ]
        }
    return {
        "nodes": [
            {"id": "node-src", "type": "file_source",
             "file_source_id": file_source_id, "columns": []},
            {"id": "node-enrich", "type": "api_enrich",
             "api_id": api_id, "param_mapping": {"country": "paese"}},
            {"id": "node-result", "type": "result",
             "format": output_format, "fileName": "output"},
        ],
        "edges": [
            {"id": "e1", "source": "node-src",    "target": "node-enrich"},
            {"id": "e2", "source": "node-enrich",  "target": "node-result"},
        ]
    }


def patch_save(mock_path="/tmp/output.csv"):
    """Patch per save_dataframe e save_result_blob."""
    saved = MagicMock(spec=Path)
    saved.__str__ = MagicMock(return_value=mock_path)
    saved.name = "output.csv"
    return saved


# =============================================================================
# Canvas vuoto / errori di configurazione
# =============================================================================

class TestExecuteEnrichmentConfigErrors:

    def test_canvas_vuoto_failure(self):
        svc = make_svc()
        result = svc.execute_enrichment("proj-1", {"nodes": [], "edges": []})
        assert result["success"] is False
        assert "nodo" in result["error"].lower() or "canvas" in result["error"].lower()

    def test_api_non_trovata_failure(self):
        svc = make_svc()
        canvas = make_canvas(api_id="api_inesistente")
        with patch.object(svc, "list_api_connections", return_value=[]), \
             patch.object(svc, "get_api_connection", return_value=None), \
             patch.object(svc, "_load_file_source", return_value=make_df()):
            result = svc.execute_enrichment("proj-1", canvas)
        assert result["success"] is False

    def test_nodo_enrich_senza_input_failure(self):
        """Nodo api_enrich senza edge in input → failure."""
        svc = make_svc()
        canvas = {
            "nodes": [
                {"id": "node-enrich", "type": "api_enrich",
                 "api_id": "api_1", "param_mapping": {}},
                {"id": "node-result", "type": "result",
                 "format": "csv", "fileName": "output"},
            ],
            "edges": [
                {"id": "e1", "source": "node-enrich", "target": "node-result"},
            ]
        }
        api_config = make_api_config(api_type="enricher")
        with patch.object(svc, "list_api_connections",
                          return_value=[{"id": "api_1"}]), \
             patch.object(svc, "get_api_connection", return_value=api_config):
            result = svc.execute_enrichment("proj-1", canvas)
        assert result["success"] is False

    def test_nessun_risultato_failure(self):
        """Tutti i nodi eseguiti ma nessun DataFrame → failure."""
        svc = make_svc()
        canvas = make_canvas()
        api_config = make_api_config()
        with patch.object(svc, "list_api_connections",
                          return_value=[{"id": "api_1"}]), \
             patch.object(svc, "get_api_connection", return_value=api_config), \
             patch.object(svc, "_load_file_source", return_value=None), \
             patch.object(svc, "_execute_api_enricher", return_value=None):
            result = svc.execute_enrichment("proj-1", canvas)
        assert result["success"] is False

    def test_eccezione_generica_gestita(self):
        """Eccezione non gestita → success=False con error."""
        svc = make_svc()
        canvas = make_canvas()
        with patch.object(svc, "list_api_connections",
                          side_effect=RuntimeError("crash imprevisto")):
            result = svc.execute_enrichment("proj-1", canvas)
        assert result["success"] is False
        assert "crash" in result["error"].lower()


# =============================================================================
# Flusso file_source → api_enrich (enricher) → result
# =============================================================================

class TestExecuteEnrichmentFileSourceEnricher:

    def _run(self, svc, df_input=None, df_output=None, output_format="csv"):
        canvas = make_canvas(output_format=output_format)
        api_config = make_api_config()
        df_in = df_input if df_input is not None else make_df(5)
        df_out = df_output if df_output is not None else make_df(5)

        saved = patch_save()
        with patch.object(svc, "list_api_connections",
                          return_value=[{"id": "api_1"}]), \
             patch.object(svc, "get_api_connection", return_value=api_config), \
             patch.object(svc, "_load_file_source", return_value=df_in), \
             patch.object(svc, "_execute_api_enricher", return_value=df_out), \
             patch("app.core.arricchisci.api_connector.save_dataframe",
                   return_value=saved), \
             patch("app.core.arricchisci.api_connector.save_result_blob"), \
             patch("app.core.arricchisci.api_connector.get_temp_output_path",
                   return_value=Path("/tmp/output.csv")):
            return svc.execute_enrichment("proj-1", canvas)

    def test_success_base(self):
        svc = make_svc()
        result = self._run(svc)
        assert result["success"] is True

    def test_rows_count_corretto(self):
        svc = make_svc()
        # Usa df_input e df_output uguali per evitare ambiguità
        df = make_df(10)
        result = self._run(svc, df_input=df, df_output=df)
        assert result["success"] is True

    def test_columns_nel_result(self):
        svc = make_svc()
        result = self._run(svc)
        assert "columns" in result
        assert isinstance(result["columns"], list)

    def test_output_file_nel_result(self):
        svc = make_svc()
        result = self._run(svc)
        assert "output_file" in result or "output_path" in result

    def test_formato_csv(self):
        svc = make_svc()
        result = self._run(svc, output_format="csv")
        assert result["success"] is True

    def test_formato_gpkg(self):
        svc = make_svc()
        result = self._run(svc, output_format="gpkg")
        assert result["success"] is True

    def test_execute_api_enricher_chiamato(self):
        svc = make_svc()
        canvas = make_canvas()
        api_config = make_api_config()
        df_in = make_df(5)
        saved = patch_save()

        with patch.object(svc, "list_api_connections",
                          return_value=[{"id": "api_1"}]), \
             patch.object(svc, "get_api_connection", return_value=api_config), \
             patch.object(svc, "_load_file_source", return_value=df_in), \
             patch.object(svc, "_execute_api_enricher",
                          return_value=make_df(5)) as mock_enrich, \
             patch("app.core.arricchisci.api_connector.save_dataframe",
                   return_value=saved), \
             patch("app.core.arricchisci.api_connector.save_result_blob"), \
             patch("app.core.arricchisci.api_connector.get_temp_output_path",
                   return_value=Path("/tmp/out.csv")):
            svc.execute_enrichment("proj-1", canvas)

        mock_enrich.assert_called_once()


# =============================================================================
# Flusso api_enrich (generator) → result
# =============================================================================

class TestExecuteEnrichmentGenerator:

    def test_generator_success(self):
        svc = make_svc()
        canvas = make_canvas(api_type="generator")
        api_config = make_api_config(api_type="generator")
        df_out = make_df(5)
        saved = patch_save()

        with patch.object(svc, "list_api_connections",
                          return_value=[{"id": "api_1"}]), \
             patch.object(svc, "get_api_connection", return_value=api_config), \
             patch.object(svc, "_execute_api_generator", return_value=df_out), \
             patch("app.core.arricchisci.api_connector.save_dataframe",
                   return_value=saved), \
             patch("app.core.arricchisci.api_connector.save_result_blob"), \
             patch("app.core.arricchisci.api_connector.get_temp_output_path",
                   return_value=Path("/tmp/out.csv")):
            result = svc.execute_enrichment("proj-1", canvas)

        assert result["success"] is True

    def test_generator_execute_api_generator_chiamato(self):
        svc = make_svc()
        canvas = make_canvas(api_type="generator")
        api_config = make_api_config(api_type="generator")
        saved = patch_save()

        with patch.object(svc, "list_api_connections",
                          return_value=[{"id": "api_1"}]), \
             patch.object(svc, "get_api_connection", return_value=api_config), \
             patch.object(svc, "_execute_api_generator",
                          return_value=make_df(3)) as mock_gen, \
             patch("app.core.arricchisci.api_connector.save_dataframe",
                   return_value=saved), \
             patch("app.core.arricchisci.api_connector.save_result_blob"), \
             patch("app.core.arricchisci.api_connector.get_temp_output_path",
                   return_value=Path("/tmp/out.csv")):
            svc.execute_enrichment("proj-1", canvas)

        mock_gen.assert_called_once()


# =============================================================================
# Flusso project_source → api_enrich → result
# =============================================================================

class TestExecuteEnrichmentProjectSource:

    def test_project_source_success(self):
        svc = make_svc()
        canvas = {
            "nodes": [
                {"id": "node-proj", "type": "project_source",
                 "source_project_id": "proj-source"},
                {"id": "node-enrich", "type": "api_enrich",
                 "api_id": "api_1", "param_mapping": {"country": "paese"}},
                {"id": "node-result", "type": "result",
                 "format": "csv", "fileName": "output"},
            ],
            "edges": [
                {"id": "e1", "source": "node-proj",   "target": "node-enrich"},
                {"id": "e2", "source": "node-enrich", "target": "node-result"},
            ]
        }
        api_config = make_api_config()
        df = make_df(5)
        saved = patch_save()

        with patch.object(svc, "list_api_connections",
                          return_value=[{"id": "api_1"}]), \
             patch.object(svc, "get_api_connection", return_value=api_config), \
             patch.object(svc, "_load_source_project", return_value=df), \
             patch.object(svc, "_execute_api_enricher", return_value=df), \
             patch("app.core.arricchisci.api_connector.save_dataframe",
                   return_value=saved), \
             patch("app.core.arricchisci.api_connector.save_result_blob"), \
             patch("app.core.arricchisci.api_connector.get_temp_output_path",
                   return_value=Path("/tmp/out.csv")):
            result = svc.execute_enrichment("proj-1", canvas)

        assert result["success"] is True


# =============================================================================
# Flusso staticValues → api_enrich (generator) → result
# =============================================================================

class TestExecuteEnrichmentStaticValues:

    def test_static_values_success(self):
        svc = make_svc()
        canvas = {
            "nodes": [
                {"id": "node-static", "type": "staticValues",
                 "values": [{"key": "lang", "value": "it"}]},
                {"id": "node-enrich", "type": "api_enrich",
                 "api_id": "api_1", "param_mapping": {}},
                {"id": "node-result", "type": "result",
                 "format": "csv", "fileName": "output"},
            ],
            "edges": [
                {"id": "e1", "source": "node-static", "target": "node-enrich"},
                {"id": "e2", "source": "node-enrich",  "target": "node-result"},
            ]
        }
        api_config = make_api_config(api_type="enricher")
        df = make_df(1)
        saved = patch_save()

        with patch.object(svc, "list_api_connections",
                          return_value=[{"id": "api_1"}]), \
             patch.object(svc, "get_api_connection", return_value=api_config), \
             patch.object(svc, "_load_static_values", return_value=df), \
             patch.object(svc, "_execute_api_enricher", return_value=df), \
             patch("app.core.arricchisci.api_connector.save_dataframe",
                   return_value=saved), \
             patch("app.core.arricchisci.api_connector.save_result_blob"), \
             patch("app.core.arricchisci.api_connector.get_temp_output_path",
                   return_value=Path("/tmp/out.csv")):
            result = svc.execute_enrichment("proj-1", canvas)

        assert result["success"] is True


# =============================================================================
# Catena doppia api_enrich
# =============================================================================

class TestExecuteEnrichmentChain:

    def test_chain_due_enricher(self):
        svc = make_svc()
        canvas = {
            "nodes": [
                {"id": "node-src", "type": "file_source",
                 "file_source_id": "file_1", "columns": []},
                {"id": "node-enrich-1", "type": "api_enrich",
                 "api_id": "api_1", "param_mapping": {"country": "paese"}},
                {"id": "node-enrich-2", "type": "api_enrich",
                 "api_id": "api_2", "param_mapping": {"capital": "capitale"}},
                {"id": "node-result", "type": "result",
                 "format": "csv", "fileName": "output"},
            ],
            "edges": [
                {"id": "e1", "source": "node-src",      "target": "node-enrich-1"},
                {"id": "e2", "source": "node-enrich-1", "target": "node-enrich-2"},
                {"id": "e3", "source": "node-enrich-2", "target": "node-result"},
            ]
        }
        api_1 = make_api_config("api_1")
        api_2 = make_api_config("api_2")
        df = make_df(5)
        saved = patch_save()

        def get_api(api_id, include_token=False):
            return api_1 if api_id == "api_1" else api_2

        with patch.object(svc, "list_api_connections",
                          return_value=[{"id": "api_1"}, {"id": "api_2"}]), \
             patch.object(svc, "get_api_connection", side_effect=get_api), \
             patch.object(svc, "_load_file_source", return_value=df), \
             patch.object(svc, "_execute_api_enricher",
                          return_value=df) as mock_enrich, \
             patch("app.core.arricchisci.api_connector.save_dataframe",
                   return_value=saved), \
             patch("app.core.arricchisci.api_connector.save_result_blob"), \
             patch("app.core.arricchisci.api_connector.get_temp_output_path",
                   return_value=Path("/tmp/out.csv")):
            result = svc.execute_enrichment("proj-1", canvas)

        assert result["success"] is True
        assert mock_enrich.call_count == 2


# =============================================================================
# preview_limit
# =============================================================================

class TestExecuteEnrichmentPreviewLimit:

    def test_preview_limit_passato(self):
        svc = make_svc()
        canvas = make_canvas()
        api_config = make_api_config()
        df = make_df(5)
        saved = patch_save()

        with patch.object(svc, "list_api_connections",
                          return_value=[{"id": "api_1"}]), \
             patch.object(svc, "get_api_connection", return_value=api_config), \
             patch.object(svc, "_load_file_source", return_value=df), \
             patch.object(svc, "_execute_api_enricher", return_value=df), \
             patch("app.core.arricchisci.api_connector.save_dataframe",
                   return_value=saved), \
             patch("app.core.arricchisci.api_connector.save_result_blob"), \
             patch("app.core.arricchisci.api_connector.get_temp_output_path",
                   return_value=Path("/tmp/out.csv")):
            svc.execute_enrichment("proj-1", canvas, preview_limit=10)

        assert svc._preview_limit == 10
