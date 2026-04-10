"""
test_A42_A54_pipeline_e2e.py — Suite ARRICCHISCI · Pipeline E2E

Prerequisiti:
  - Backend FastAPI su localhost:8000 (con Celery ALWAYS_EAGER)
  - Mock server su localhost:8999  →  uvicorn mock_api_server:app --port 8999

Struttura risultato pipeline_task per progetti arricchisci:
    { success, project_id, rows_count, columns, message, output_file }

Test coperti:
  A-42  FileSource → Enricher → CSV
  A-43  FileSource → Enricher → GPKG geometria WKT
  A-44  ProjectSource da ESTRAI PostgreSQL
  A-45  Stima tempo POST /enrichment/estimate
  A-46  Validazione: POST /enrichment/validate
  A-47  Output mapping path JSON annidato [0].capital[0]
  A-48  continue_on_error: righe 404 → null, pipeline non si blocca
  A-49  Cache dedup: 10 righe 3 unici → 3 chiamate API (verificato via mock)
  A-50  Catena enricher: simple → weather (capitale come parametro)
  A-51  Generator puro senza FileSource (api_type=generator)
  A-52  N/A — skippato (alias output_mapping: verificabile solo da UI)
  A-53  Batch: batch_size=5
  A-54  max_errors=2: pipeline si ferma al terzo errore
"""

import pytest
import requests

from conftest import (
    build_arricchisci_canvas,
    build_arricchisci_canvas_chain,
    build_arricchisci_canvas_no_src,
    execute_arricchisci,
    build_estrai_canvas,
    poll_task,
    MOCK_URL,
)


# =============================================================================
# A-42 — FileSource → Enricher → CSV
# =============================================================================

class TestA42:
    """FileSource CSV 10 paesi → /mock/simple → output CSV con capitale e popolazione."""

    def test_a42_esecuzione_success(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[
                {"path": "capital",    "column": "capitale"},
                {"path": "population", "column": "popolazione"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-42 FileSource→CSV",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi,
                api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a42_righe_output(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-42 rows",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi,
                api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result.get("rows_count", 0) == 10

    def test_a42_colonne_arricchite_presenti(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[
                {"path": "capital",    "column": "capitale"},
                {"path": "population", "column": "popolazione"},
                {"path": "area",       "column": "area_kmq"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-42 colonne",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi,
                api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        columns = result.get("columns", [])
        assert "capitale"    in columns
        assert "popolazione" in columns
        assert "area_kmq"    in columns

    def test_a42_zero_errori_mock(self, client, make_project, make_api_connection, csv_paesi):
        """Tutti i 10 paesi sono validi → il mock counter non deve avere errori."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/counter",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-42 no errors",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi,
                api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True
        stats = requests.get(f"{MOCK_URL}/mock/counter/stats").json()
        assert stats["total_calls"] == 10


# =============================================================================
# A-43 — FileSource → Enricher → GPKG con geometria WKT
# =============================================================================

class TestA43:
    """FileSource CSV città → /mock/geometry → output GPKG con lat/lon."""

    def test_a43_output_gpkg_success(self, client, make_project, make_api_connection, csv_citta):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/geometry",
            output_mapping=[
                {"path": "lat",         "column": "lat"},
                {"path": "lon",         "column": "lon"},
                {"path": "temperature", "column": "temperatura"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-43 GPKG geometria",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_citta,
                api_id=api_id,
                param_mapping={"city": "citta"},
                output_format="gpkg",
                output_filename="a43_geo",
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a43_colonne_lat_lon(self, client, make_project, make_api_connection, csv_citta):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/geometry",
            output_mapping=[
                {"path": "lat", "column": "lat"},
                {"path": "lon", "column": "lon"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-43 lat lon",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_citta,
                api_id=api_id,
                param_mapping={"city": "citta"},
                output_format="gpkg",
            ),
        )
        result = execute_arricchisci(client, project_id)
        columns = result.get("columns", [])
        assert "lat" in columns
        assert "lon" in columns

    def test_a43_righe_output(self, client, make_project, make_api_connection, csv_citta):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/geometry",
            output_mapping=[
                {"path": "lat", "column": "lat"},
                {"path": "lon", "column": "lon"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-43 rows",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_citta,
                api_id=api_id,
                param_mapping={"city": "citta"},
                output_format="gpkg",
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result.get("rows_count", 0) == 10


# =============================================================================
# A-44 — ProjectSource da ESTRAI PostgreSQL
# =============================================================================

class TestA44:
    """ProjectSource: output di un progetto ESTRAI usato come sorgente ARRICCHISCI."""

    def test_a44_project_source_success(self, client, make_project, make_api_connection, pg_conn, pg_table):
        table_name = pg_table(
            ddl="id SERIAL PRIMARY KEY, paese TEXT, codice TEXT",
            rows=[
                "INSERT INTO {table} (paese, codice) VALUES "
                "('Italy','IT'),('France','FR'),('Germany','DE'),"
                "('Spain','ES'),('Portugal','PT')"
            ],
        )
        estrai_id = make_project(
            name="[AUTO] A-44 ESTRAI sorgente",
            project_type="estrai",
            connection_id=pg_conn,
            canvas=build_estrai_canvas(
                table_name=table_name,
                columns=[
                    {"name": "paese",  "type": "string", "visible": True},
                    {"name": "codice", "type": "string", "visible": True},
                ],
            ),
        )
        res = client.post(f"/projects/{estrai_id}/execute")
        assert res.status_code == 200
        poll_task(client, res.json()["task_id"])

        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        canvas = {
            "nodes": [
                {
                    "id":               "node-src",
                    "type":             "project_source",
                    "position":         {"x": 100, "y": 200},
                    "source_project_id": estrai_id,
                    "columns":          [],
                },
                {
                    "id":               "node-enrich",
                    "type":             "api_enrich",
                    "position":         {"x": 400, "y": 200},
                    "api_id":           api_id,
                    "param_mapping":    {"country": "paese"},
                    "continue_on_error": False,
                    "parallel_workers": 1,
                },
                {
                    "id":       "node-result",
                    "type":     "result",
                    "position": {"x": 700, "y": 200},
                    "fileName": "a44_output",
                    "format":   "csv",
                },
            ],
            "edges": [
                {"id": "e1", "source": "node-src",   "target": "node-enrich"},
                {"id": "e2", "source": "node-enrich", "target": "node-result"},
            ],
        }
        project_id = make_project(
            name="[AUTO] A-44 ProjectSource",
            project_type="arricchisci",
            canvas=canvas,
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a44_righe_output(self, client, make_project, make_api_connection, pg_conn, pg_table):
        table_name = pg_table(
            ddl="id SERIAL PRIMARY KEY, paese TEXT",
            rows=["INSERT INTO {table} (paese) VALUES ('Italy'),('France'),('Germany')"],
        )
        estrai_id = make_project(
            name="[AUTO] A-44 righe",
            project_type="estrai",
            connection_id=pg_conn,
            canvas=build_estrai_canvas(
                table_name=table_name,
                columns=[{"name": "paese", "type": "string", "visible": True}],
            ),
        )
        res = client.post(f"/projects/{estrai_id}/execute")
        assert res.status_code == 200
        poll_task(client, res.json()["task_id"])

        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        canvas = {
            "nodes": [
                {"id": "node-src",    "type": "project_source", "position": {"x": 100, "y": 200},
                 "source_project_id": estrai_id, "columns": []},
                {"id": "node-enrich", "type": "api_enrich",     "position": {"x": 400, "y": 200},
                 "api_id": api_id, "param_mapping": {"country": "paese"},
                 "continue_on_error": False, "parallel_workers": 1},
                {"id": "node-result", "type": "result",         "position": {"x": 700, "y": 200},
                 "fileName": "a44_rows", "format": "csv"},
            ],
            "edges": [
                {"id": "e1", "source": "node-src",   "target": "node-enrich"},
                {"id": "e2", "source": "node-enrich", "target": "node-result"},
            ],
        }
        project_id = make_project(
            name="[AUTO] A-44 rows", project_type="arricchisci", canvas=canvas,
        )
        result = execute_arricchisci(client, project_id)
        assert result.get("rows_count", 0) == 3


# =============================================================================
# A-45 — Stima tempo POST /enrichment/estimate
# =============================================================================

class TestA45:
    """POST /enrichment/estimate → stima righe, chiamate uniche, risparmio, tempo."""

    def test_a45_endpoint_risponde(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-45 estimate",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        res = client.post("/enrichment/estimate", json={
            "project_id":    project_id,
            "api_id":        api_id,
            "param_mapping": {"country": "paese"},
            "file_source_id": csv_paesi,
        })
        assert res.status_code == 200

    def test_a45_stima_righe(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-45 total rows",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        res = client.post("/enrichment/estimate", json={
            "project_id":    project_id,
            "api_id":        api_id,
            "param_mapping": {"country": "paese"},
            "file_source_id": csv_paesi,
        })
        assert res.json().get("total_rows") == 10

    def test_a45_risparmio_cache_dedup(self, client, make_project, make_api_connection, csv_paesi_ripetuti):
        """10 righe con 3 unici → risparmio dedup ~70%."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-45 dedup savings",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi_ripetuti, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        res = client.post("/enrichment/estimate", json={
            "project_id":    project_id,
            "api_id":        api_id,
            "param_mapping": {"country": "paese"},
            "file_source_id": csv_paesi_ripetuti,
        })
        body = res.json()
        assert body.get("unique_combinations") == 3
        assert body.get("deduplication_savings_percent", 0) >= 60

    def test_a45_tempo_stimato_presente(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-45 time",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        res = client.post("/enrichment/estimate", json={
            "project_id":    project_id,
            "api_id":        api_id,
            "param_mapping": {"country": "paese"},
            "file_source_id": csv_paesi,
        })
        assert "estimated_time_seconds" in res.json()


# =============================================================================
# A-46 — Validazione canvas POST /enrichment/validate
# =============================================================================

class TestA46:
    """Validazione: param mancanti → valid=False, canvas corretto → valid=True."""

    def test_a46_canvas_valido_passa(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-46 valid",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        res = client.post("/enrichment/validate", json={
            "project_id":    project_id,
            "api_id":        api_id,
            "param_mapping": {"country": "paese"},
            "file_source_id": csv_paesi,
        })
        assert res.status_code == 200
        assert res.json().get("valid") is True

    def test_a46_colonna_mancante_restituisce_errore(self, client, make_project, make_api_connection, csv_paesi):
        """Colonna 'colonna_inesistente' non presente nel CSV → valid=False."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-46 missing col",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "colonna_inesistente"},
            ),
        )
        res = client.post("/enrichment/validate", json={
            "project_id":    project_id,
            "api_id":        api_id,
            "param_mapping": {"country": "colonna_inesistente"},
            "file_source_id": csv_paesi,
        })
        body = res.json()
        assert body.get("valid") is False
        assert len(body.get("missing_columns", [])) > 0

    def test_a46_null_rows_contati(self, client, make_project, make_api_connection, csv_paesi_con_null):
        """CSV con 2 null nel parametro → valid=True ma null_param_rows_count=2."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-46 null rows",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi_con_null, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        res = client.post("/enrichment/validate", json={
            "project_id":    project_id,
            "api_id":        api_id,
            "param_mapping": {"country": "paese"},
            "file_source_id": csv_paesi_con_null,
        })
        body = res.json()
        assert body.get("null_param_rows_count", 0) == 2


# =============================================================================
# A-47 — Output mapping path JSON annidato
# =============================================================================

class TestA47:
    """/mock/nested → path annidati [0].capital[0], [0].name.common, [0].population."""

    def test_a47_path_annidato_success(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/nested",
            output_mapping=[
                {"path": "[0].name.common", "column": "nome_comune"},
                {"path": "[0].capital[0]",  "column": "capitale"},
                {"path": "[0].population",  "column": "popolazione"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-47 nested path",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a47_colonne_estratte(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/nested",
            output_mapping=[
                {"path": "[0].capital[0]", "column": "capitale"},
                {"path": "[0].population", "column": "popolazione"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-47 colonne estratte",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        columns = result.get("columns", [])
        assert "capitale"    in columns
        assert "popolazione" in columns

    def test_a47_righe_output(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/nested",
            output_mapping=[{"path": "[0].capital[0]", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-47 rows",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result.get("rows_count", 0) == 10


# =============================================================================
# A-48 — continue_on_error
# =============================================================================

class TestA48:
    """continue_on_error=True: righe 404 → null, pipeline non si blocca."""

    def test_a48_pipeline_non_crashata(self, client, make_project, make_api_connection, csv_paesi_con_invalidi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/strict",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-48 continue_on_error",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi_con_invalidi, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a48_tutte_le_righe_in_output(self, client, make_project, make_api_connection, csv_paesi_con_invalidi):
        """6 righe nel CSV → 6 righe in output (3 con capitale null per i 404)."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/strict",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-48 rows output",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi_con_invalidi, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result.get("rows_count", 0) == 6

    def test_a48_mock_ha_ricevuto_tutte_le_chiamate(self, client, make_project, make_api_connection, csv_paesi_con_invalidi):
        """Il mock deve aver ricevuto 6 chiamate (3 ok + 3 → 404)."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/counter",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-48 mock calls",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi_con_invalidi, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
            ),
        )
        execute_arricchisci(client, project_id)
        stats = requests.get(f"{MOCK_URL}/mock/counter/stats").json()
        assert stats["total_calls"] == 6

    def test_a48_senza_continue_on_error_fallisce(self, client, make_project, make_api_connection, csv_paesi_con_invalidi):
        """Con continue_on_error=False il primo 404 ferma la pipeline."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/strict",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-48 no continue",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi_con_invalidi, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=False,
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is False


# =============================================================================
# A-49 — Cache dedup
# =============================================================================

class TestA49:
    """10 righe con 3 unici → solo 3 chiamate API effettive al mock counter."""

    def test_a49_esecuzione_success(self, client, make_project, make_api_connection, csv_paesi_ripetuti):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/counter",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-49 cache dedup",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi_ripetuti, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a49_solo_3_chiamate_api(self, client, make_project, make_api_connection, csv_paesi_ripetuti):
        """Cache dedup: 10 righe con 3 unici → esattamente 3 chiamate al mock."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/counter",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-49 3 calls",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi_ripetuti, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        execute_arricchisci(client, project_id)
        stats = requests.get(f"{MOCK_URL}/mock/counter/stats").json()
        assert stats["total_calls"] == 3

    def test_a49_output_10_righe(self, client, make_project, make_api_connection, csv_paesi_ripetuti):
        """Con cache: 3 chiamate ma output deve avere 10 righe (cache riutilizzata)."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/counter",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-49 10 rows",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi_ripetuti, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result.get("rows_count", 0) == 10

    def test_a49_estimate_conferma_3_chiamate(self, client, make_project, make_api_connection, csv_paesi_ripetuti):
        """L'endpoint estimate deve prevedere 3 chiamate uniche su 10 righe."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-49 estimate",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi_ripetuti, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        res = client.post("/enrichment/estimate", json={
            "project_id":     project_id,
            "api_id":         api_id,
            "param_mapping":  {"country": "paese"},
            "file_source_id": csv_paesi_ripetuti,
        })
        assert res.json().get("unique_combinations") == 3


# =============================================================================
# A-50 — Catena enricher
# =============================================================================

class TestA50:
    """FileSource → /mock/simple (capitale) → /mock/weather (temperatura) → colonne da entrambe."""

    def test_a50_catena_success(self, client, make_project, make_api_connection, csv_paesi):
        api_id_1 = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        api_id_2 = make_api_connection(
            url=f"{MOCK_URL}/mock/weather",
            output_mapping=[
                {"path": "temperature", "column": "temperatura"},
                {"path": "windspeed",   "column": "vento"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-50 catena",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas_chain(
                file_source_id=csv_paesi,
                api_id_1=api_id_1,
                api_id_2=api_id_2,
                param_mapping_1={"country": "paese"},
                param_mapping_2={"city":    "capitale"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a50_colonne_da_entrambe_le_api(self, client, make_project, make_api_connection, csv_paesi):
        api_id_1 = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        api_id_2 = make_api_connection(
            url=f"{MOCK_URL}/mock/weather",
            output_mapping=[{"path": "temperature", "column": "temperatura"}],
        )
        project_id = make_project(
            name="[AUTO] A-50 colonne chain",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas_chain(
                file_source_id=csv_paesi,
                api_id_1=api_id_1,
                api_id_2=api_id_2,
                param_mapping_1={"country": "paese"},
                param_mapping_2={"city":    "capitale"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        columns = result.get("columns", [])
        assert "capitale"    in columns
        assert "temperatura" in columns


# =============================================================================
# A-51 — Generator puro senza FileSource
# =============================================================================

class TestA51:
    """api_type=generator: il nodo api_enrich genera dati dalla paginazione senza input."""

    def test_a51_generator_success(self, client, make_project, make_api_connection):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/generator",
            api_type="generator",
            output_mapping=[
                {"path": "id",             "column": "id"},
                {"path": "synthetic_name", "column": "nome"},
                {"path": "score",          "column": "punteggio"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-51 generator puro",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas_no_src(
                api_id=api_id,
                output_format="csv",
                output_filename="a51_generator",
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a51_output_non_vuoto(self, client, make_project, make_api_connection):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/generator",
            api_type="generator",
            output_mapping=[{"path": "id", "column": "id"}],
        )
        project_id = make_project(
            name="[AUTO] A-51 non vuoto",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas_no_src(api_id=api_id),
        )
        result = execute_arricchisci(client, project_id)
        assert result.get("rows_count", 0) > 0

    def test_a51_colonne_presenti(self, client, make_project, make_api_connection):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/generator",
            api_type="generator",
            output_mapping=[
                {"path": "id",    "column": "id"},
                {"path": "score", "column": "punteggio"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-51 colonne",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas_no_src(api_id=api_id),
        )
        result = execute_arricchisci(client, project_id)
        columns = result.get("columns", [])
        assert "id"        in columns
        assert "punteggio" in columns


@pytest.mark.skip(reason="A-52 N/A — alias output_mapping verificabile solo da UI")
def test_a52_alias_output_mapping():
    pass


# =============================================================================
# A-53 — Batch
# =============================================================================

class TestA53:
    """batch nativo: batch_size=5, 10 righe → 2 chiamate API (5+5)."""

    def _batch_api_id(self, make_api_connection):
        """Crea connessione API con batch config abilitata."""
        return make_api_connection(
            url=f"{MOCK_URL}/mock/batch",
            method="POST",
            output_mapping=[
                {"path": "capital",    "column": "capitale"},
                {"path": "population", "column": "popolazione"},
            ],
            options={"timeout": 10, "max_retries": 1},
            extra_payload={
                "batch": {
                    "enabled": True,
                    "max_size": 5,
                    "input_key": "items",
                    "input_format": "array",
                    "output_path": "",
                },
            },
        )

    def test_a53_batch_success(self, client, make_project, make_api_connection, csv_paesi):
        api_id = self._batch_api_id(make_api_connection)
        project_id = make_project(
            name="[AUTO] A-53 batch",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a53_output_10_righe(self, client, make_project, make_api_connection, csv_paesi):
        api_id = self._batch_api_id(make_api_connection)
        project_id = make_project(
            name="[AUTO] A-53 rows",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result.get("rows_count", 0) == 10


# =============================================================================
# A-54 — max_errors
# =============================================================================

class TestA54:
    """max_errors=2: pipeline si ferma quando gli errori superano la soglia."""

    def test_a54_pipeline_si_ferma(self, client, make_project, make_api_connection, csv_paesi_con_invalidi):
        """CSV con 3 invalidi, max_errors=2 → fallisce dopo il terzo errore."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/strict",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-54 max_errors=2",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi_con_invalidi, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
                max_errors=2,
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is False

    def test_a54_max_errors_alto_permette_completamento(self, client, make_project, make_api_connection, csv_paesi_con_invalidi):
        """max_errors=10 (> errori reali=3) → pipeline completa con success=True."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/strict",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-54 max_errors alto",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi_con_invalidi, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
                max_errors=10,
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a54_max_errors_1_blocca_al_primo(self, client, make_project, make_api_connection, csv_paesi_con_invalidi):
        """max_errors=1 → si ferma dopo il primo errore."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/strict",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-54 max_errors=1",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi_con_invalidi, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
                max_errors=1,
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is False
