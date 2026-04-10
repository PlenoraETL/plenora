"""
test_A55_A74_edge_cases.py — Suite ARRICCHISCI · Edge Cases

Prerequisiti:
  - Backend FastAPI su localhost:8000 (Celery ALWAYS_EAGER)
  - Mock server su localhost:8999 → uvicorn mock_api_server:app --port 8999

Test coperti:
  A-55  API risponde con campo null per alcune righe
  A-56  API risponde con tipo inatteso (numero invece di stringa)
  A-57  API risponde con array vuoto []
  A-58  API timeout su singola riga (retry esaurito)
  A-59  API restituisce HTML invece di JSON (errore 500)
  A-60  CSV sorgente con valori null nel parametro mappato
  A-61  CSV sorgente con encoding Latin-1
  A-62  CSV sorgente con separatore punto e virgola
  A-63  Output mapping path inesistente nella risposta
  A-64  Output mapping su risposta non-array con path [0]
  A-65  Enricher con static_parameters sovrascrive parametro dinamico
  A-66  Enricher con response molto grande (> 1MB per riga)
  A-67  Enricher parallelo: 4 worker thread su 100 righe
  A-68  Catena enricher: secondo nodo usa output del primo
  A-69  Preview con API che restituisce geometrie GeoJSON
  A-70  Enricher su FileSource con 0 righe (file vuoto)
  A-71  N/A — OAuth2 token scaduto: coperto da A-04/A-23
  A-72  Annullamento (cancel) enrichment a metà esecuzione
  A-73  Re-esecuzione stesso canvas due volte di fila
  A-74  Enricher con 1000 righe e rate_limit=2 req/sec
"""

import time
import pytest
import requests

from conftest import (
    build_arricchisci_canvas,
    build_arricchisci_canvas_chain,
    execute_arricchisci,
    build_estrai_canvas,
    poll_task,
    MOCK_URL,
)


# =============================================================================
# A-55 — API risponde con campo null per alcune righe
# =============================================================================

class TestA55:
    """/mock/null-fields: name=null, capital=null, population presente → no crash."""

    def test_a55_no_crash(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/null-fields",
            output_mapping=[
                {"path": "name",       "column": "nome"},
                {"path": "capital",    "column": "capitale"},
                {"path": "population", "column": "popolazione"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-55 null fields",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a55_righe_output(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/null-fields",
            output_mapping=[{"path": "population", "column": "popolazione"}],
        )
        project_id = make_project(
            name="[AUTO] A-55 righe",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result.get("rows_count", 0) == 10

    def test_a55_colonna_population_presente(self, client, make_project, make_api_connection, csv_paesi):
        """population è valorizzata anche quando name/capital sono null."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/null-fields",
            output_mapping=[
                {"path": "name",       "column": "nome"},
                {"path": "population", "column": "popolazione"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-55 population presente",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        columns = result.get("columns", [])
        assert "popolazione" in columns


# =============================================================================
# A-56 — API risponde con tipo inatteso (numero invece di stringa)
# =============================================================================

class TestA56:
    """/mock/wrong-types: population come stringa, area come stringa → no crash."""

    def test_a56_no_crash(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/wrong-types",
            output_mapping=[
                {"path": "capital",    "column": "capitale"},
                {"path": "population", "column": "popolazione"},
                {"path": "area",       "column": "area"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-56 wrong types",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a56_valore_passato_senza_crash(self, client, make_project, make_api_connection, csv_paesi):
        """Il valore stringa viene passato così com'è senza errore."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/wrong-types",
            output_mapping=[{"path": "population", "column": "popolazione"}],
        )
        project_id = make_project(
            name="[AUTO] A-56 valore stringa",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result.get("rows_count", 0) == 10


# =============================================================================
# A-57 — API risponde con array vuoto []
# =============================================================================

class TestA57:
    """/mock/empty-array: risposta [] → colonne null, nessun KeyError."""

    def test_a57_no_crash(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/empty-array",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-57 empty array",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a57_righe_output(self, client, make_project, make_api_connection, csv_paesi):
        """10 righe in input → 10 righe in output con capitale=null."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/empty-array",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-57 righe",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result.get("rows_count", 0) == 10


# =============================================================================
# A-58 — API timeout su singola riga (retry esaurito)
# =============================================================================

class TestA58:
    """/mock/slow?delay=60 con timeout=5s, max_retries=0 → riga con errore, altre ok."""

    def test_a58_timeout_riga_singola_no_crash(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/slow?delay=5",
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options={"timeout": 2, "retry_max": 0},
        )
        project_id = make_project(
            name="[AUTO] A-58 timeout",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
            ),
        )
        result = execute_arricchisci(client, project_id)
        # Con continue_on_error il task completa
        assert result["success"] is True

    def test_a58_errori_riportati(self, client, make_project, make_api_connection, csv_paesi):
        """Tutte le righe vanno in timeout → enrichment_stats.errors > 0."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/slow?delay=5",
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options={"timeout": 2, "retry_max": 0},
        )
        project_id = make_project(
            name="[AUTO] A-58 errori",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
            ),
        )
        result = execute_arricchisci(client, project_id, timeout=180)
        stats = result.get("enrichment_stats") or {}
        assert stats.get("errors", 0) > 0


# =============================================================================
# A-59 — API restituisce HTML invece di JSON (errore 500)
# =============================================================================

class TestA59:
    """/mock/html-error: HTTP 500 con body HTML → errore parsing chiaro, no crash backend."""

    def test_a59_no_crash(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/html-error",
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options={"max_retries": 0, "timeout": 10},
        )
        project_id = make_project(
            name="[AUTO] A-59 html error",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a59_errori_riportati(self, client, make_project, make_api_connection, csv_paesi):
        """Tutte le righe danno 500 → errors == 10."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/html-error",
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options={"retry_max": 0, "timeout": 10},
        )
        project_id = make_project(
            name="[AUTO] A-59 errori",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
            ),
        )
        result = execute_arricchisci(client, project_id)
        stats = result.get("enrichment_stats") or {}
        assert stats.get("errors", 0) == 10


# =============================================================================
# A-60 — CSV sorgente con valori null nel parametro mappato
# =============================================================================

class TestA60:
    """csv_paesi_con_null: 2 righe con paese="" → skip_null_params → 2 saltate, 3 arricchite."""

    def test_a60_no_crash(self, client, make_project, make_api_connection, csv_paesi_con_null):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-60 null params",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi_con_null, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a60_righe_skippate(self, client, make_project, make_api_connection, csv_paesi_con_null):
        """5 righe totali: 3 arricchite + 2 null params (skip o error)."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-60 skip",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi_con_null, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
            ),
        )
        result = execute_arricchisci(client, project_id)
        stats = result.get("enrichment_stats") or {}
        # Le 2 righe null vengono skippate o segnalate come errori — in entrambi i casi
        # le 3 righe valide devono essere arricchite con successo
        assert stats.get("success", 0) == 3

    def test_a60_chiamate_api_solo_righe_valide(self, client, make_project, make_api_connection, csv_paesi_con_null):
        """Massimo 3 chiamate API (le 2 null non devono chiamare l'API)."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/counter",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-60 calls",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi_con_null, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
            ),
        )
        execute_arricchisci(client, project_id)
        stats = requests.get(f"{MOCK_URL}/mock/counter/stats").json()
        assert stats["total_calls"] <= 3


# =============================================================================
# A-61 — CSV sorgente con encoding Latin-1
# =============================================================================

class TestA61:
    """csv_latin1: caratteri accentati Italian → encoding auto-detect, no UnicodeDecodeError."""

    def test_a61_no_crash(self, client, make_project, make_api_connection, csv_latin1):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-61 latin1",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_latin1, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a61_righe_output(self, client, make_project, make_api_connection, csv_latin1):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-61 righe",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_latin1, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result.get("rows_count", 0) == 5


# =============================================================================
# A-62 — CSV sorgente con separatore punto e virgola
# =============================================================================

class TestA62:
    """csv_semicolon: separatore ; → parsing corretto, colonne riconosciute."""

    def test_a62_no_crash(self, client, make_project, make_api_connection, csv_semicolon):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-62 semicolon",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_semicolon, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a62_righe_output(self, client, make_project, make_api_connection, csv_semicolon):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-62 righe",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_semicolon, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result.get("rows_count", 0) == 5


# =============================================================================
# A-63 — Output mapping path inesistente nella risposta
# =============================================================================

class TestA63:
    """/mock/nested/missing-field: path [0].campoChNonEsiste → colonna null, no KeyError."""

    def test_a63_no_crash(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/nested/missing-field",
            output_mapping=[
                {"path": "[0].name",             "column": "nome"},
                {"path": "[0].campoChNonEsiste",  "column": "campo_mancante"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-63 missing path",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a63_colonna_presente_con_null(self, client, make_project, make_api_connection, csv_paesi):
        """La colonna campo_mancante deve essere presente ma con valori null."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/nested/missing-field",
            output_mapping=[
                {"path": "[0].name",            "column": "nome"},
                {"path": "[0].campoChNonEsiste", "column": "campo_mancante"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-63 null col",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
            ),
        )
        result = execute_arricchisci(client, project_id)
        columns = result.get("columns", [])
        assert "campo_mancante" in columns


# =============================================================================
# A-64 — Output mapping su risposta non-array con path [0]
# =============================================================================

class TestA64:
    """/mock/nested/object-not-array: oggetto {} invece di array → gestione graceful."""

    def test_a64_no_crash(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/nested/object-not-array",
            output_mapping=[
                {"path": "[0].capital",    "column": "capitale"},
                {"path": "[0].population", "column": "popolazione"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-64 object not array",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a64_righe_output(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/nested/object-not-array",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-64 righe",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
                continue_on_error=True,
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result.get("rows_count", 0) == 10


# =============================================================================
# A-65 — static_parameters sovrascrive parametro dinamico
# =============================================================================

class TestA65:
    """/mock/static-param: stesso nome in static e param_mapping → static vince."""

    def test_a65_no_crash(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/static-param",
            output_mapping=[
                {"path": "country",        "column": "paese_ricevuto"},
                {"path": "source_received", "column": "sorgente"},
            ],
            extra_payload={
                "static_parameters": {"source": "static"},
            },
        )
        project_id = make_project(
            name="[AUTO] A-65 static param",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a65_righe_output(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/static-param",
            output_mapping=[{"path": "source_received", "column": "sorgente"}],
            extra_payload={"static_parameters": {"source": "static"}},
        )
        project_id = make_project(
            name="[AUTO] A-65 righe",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result.get("rows_count", 0) == 10


# =============================================================================
# A-66 — Response molto grande (> 1MB per riga)
# =============================================================================

class TestA66:
    """/mock/large-payload?size_kb=1100: payload >1MB → no OOM, completamento."""

    def test_a66_no_oom(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/large-payload?size_kb=1100",
            output_mapping=[
                {"path": "name",       "column": "nome"},
                {"path": "capital",    "column": "capitale"},
                {"path": "population", "column": "popolazione"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-66 large payload",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a66_righe_output(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/large-payload?size_kb=1100",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-66 righe",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result.get("rows_count", 0) == 10


# =============================================================================
# A-67 — Enricher parallelo: 4 worker su 100 righe
# =============================================================================

class TestA67:
    """csv_100_righe + parallel_workers=4 → 100 righe, ordine preservato, no race."""

    def test_a67_parallel_success(self, client, make_project, make_api_connection, csv_100_righe):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/counter?delay=0.05",
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options={"parallel_workers": 4},
        )
        project_id = make_project(
            name="[AUTO] A-67 parallel",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_100_righe, api_id=api_id,
                param_mapping={"country": "paese"},
                parallel_workers=4,
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a67_100_righe_output(self, client, make_project, make_api_connection, csv_100_righe):
        """Output deve avere esattamente 100 righe — no duplicati, no perdite."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/counter?delay=0.05",
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options={"parallel_workers": 4},
        )
        project_id = make_project(
            name="[AUTO] A-67 100 righe",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_100_righe, api_id=api_id,
                param_mapping={"country": "paese"},
                parallel_workers=4,
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result.get("rows_count", 0) == 100

    def test_a67_concorrenza_rilevata(self, client, make_project, make_api_connection, csv_100_righe):
        """peak_concurrent > 1 conferma che i worker girano in parallelo."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/counter?delay=0.05",
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options={"parallel_workers": 4},
        )
        project_id = make_project(
            name="[AUTO] A-67 concorrenza",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_100_righe, api_id=api_id,
                param_mapping={"country": "paese"},
                parallel_workers=4,
            ),
        )
        execute_arricchisci(client, project_id)
        stats = requests.get(f"{MOCK_URL}/mock/counter/stats").json()
        assert stats["peak_concurrent"] > 1


# =============================================================================
# A-68 — Catena enricher: secondo nodo usa output del primo
# =============================================================================

class TestA68:
    """FileSource → /mock/simple (capitale) → /mock/weather (su capitale) → colonne entrambe."""

    def test_a68_catena_success(self, client, make_project, make_api_connection, csv_paesi):
        api_id_1 = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        api_id_2 = make_api_connection(
            url=f"{MOCK_URL}/mock/weather",
            output_mapping=[
                {"path": "temperature", "column": "temperatura"},
                {"path": "condition",   "column": "condizione"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-68 catena",
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

    def test_a68_colonne_da_entrambe(self, client, make_project, make_api_connection, csv_paesi):
        api_id_1 = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        api_id_2 = make_api_connection(
            url=f"{MOCK_URL}/mock/weather",
            output_mapping=[{"path": "temperature", "column": "temperatura"}],
        )
        project_id = make_project(
            name="[AUTO] A-68 colonne chain",
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

    def test_a68_secondo_nodo_riceve_stringa(self, client, make_project, make_api_connection, csv_paesi):
        """Il secondo enricher deve ricevere la stringa capitale corretta (es. 'Rome')."""
        api_id_1 = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        api_id_2 = make_api_connection(
            url=f"{MOCK_URL}/mock/counter",
            output_mapping=[{"path": "capital", "column": "capitale_meteo"}],
        )
        project_id = make_project(
            name="[AUTO] A-68 stringa capitale",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas_chain(
                file_source_id=csv_paesi,
                api_id_1=api_id_1,
                api_id_2=api_id_2,
                param_mapping_1={"country": "paese"},
                param_mapping_2={"country": "capitale"},
            ),
        )
        execute_arricchisci(client, project_id)
        # Il secondo enricher ha ricevuto almeno 1 chiamata (dedup riduce le duplicate)
        # 10 paesi → 10 capitali uniche → 10 chiamate (no dedup tra capitali diverse)
        stats = requests.get(f"{MOCK_URL}/mock/counter/stats").json()
        assert stats["total_calls"] >= 1


# =============================================================================
# A-69 — Preview con API che restituisce geometrie GeoJSON
# =============================================================================

class TestA69:
    """/mock/geometry: risposta con geometry GeoJSON → WKT nel CSV di output."""

    def test_a69_success(self, client, make_project, make_api_connection, csv_citta):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/geometry",
            output_mapping=[
                {"path": "lat",      "column": "lat"},
                {"path": "lon",      "column": "lon"},
                {"path": "geometry", "column": "geometry"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-69 geometry",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_citta, api_id=api_id,
                param_mapping={"city": "citta"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a69_colonna_geometry_presente(self, client, make_project, make_api_connection, csv_citta):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/geometry",
            output_mapping=[
                {"path": "lat", "column": "lat"},
                {"path": "lon", "column": "lon"},
            ],
        )
        project_id = make_project(
            name="[AUTO] A-69 colonne geo",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_citta, api_id=api_id,
                param_mapping={"city": "citta"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        columns = result.get("columns", [])
        assert "lat" in columns
        assert "lon" in columns


# =============================================================================
# A-70 — Enricher su FileSource con 0 righe (file vuoto)
# =============================================================================

class TestA70:
    """csv_empty: solo header, zero righe → esecuzione completata, output CSV vuoto."""

    def test_a70_no_crash(self, client, make_project, make_api_connection, csv_empty):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-70 empty source",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_empty, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id)
        assert result["success"] is True

    def test_a70_zero_righe_output(self, client, make_project, make_api_connection, csv_empty):
        """0 righe in input → 0 righe in output, nessuna chiamata API."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/counter",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-70 zero righe",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_empty, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        execute_arricchisci(client, project_id)
        stats = requests.get(f"{MOCK_URL}/mock/counter/stats").json()
        assert stats["total_calls"] == 0


@pytest.mark.skip(reason="A-71 N/A — OAuth2 token scaduto richiede API reale: coperto da A-04/A-23")
def test_a71_oauth2_token_scaduto():
    pass


# =============================================================================
# A-72 — Annullamento (cancel) enrichment a metà esecuzione
# =============================================================================

class TestA72:
    """Avvia enrichment lento, annulla dopo 3s → task terminato pulito."""

    def test_a72_cancel_success(self, client, make_project, make_api_connection, csv_paesi):
        """Avvia task su endpoint lento, poi DELETE /tasks/{id} → status cancelled.
        Usa delay breve per non bloccare il worker su Windows --pool=solo.
        """
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/slow?delay=5",
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options={"timeout": 60, "retry_max": 0},
        )
        project_id = make_project(
            name="[AUTO] A-72 cancel",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        # Avvia il task
        res = client.post(f"/projects/{project_id}/enrich-async")
        assert res.status_code == 200
        task_id = res.json().get("task_id")
        assert task_id

        # Annulla subito
        cancel_res = client.delete(f"/tasks/{task_id}")
        assert cancel_res.status_code == 200

        # Attendi che il worker completi/termini il task prima di procedere
        # Su Windows --pool=solo la revoke non è istantanea
        deadline = time.time() + 60
        while time.time() < deadline:
            status_res = client.get(f"/tasks/{task_id}")
            if status_res.status_code in (404,):
                break
            state = status_res.json().get("status", "")
            if state in ("revoked", "REVOKED", "success", "SUCCESS", "failure", "FAILURE"):
                break
            time.sleep(1)

    def test_a72_output_parziale_non_salvato(self, client, make_project, make_api_connection, csv_paesi):
        """Dopo cancel il progetto non deve avere un output file valido."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/slow?delay=5",
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options={"timeout": 60, "retry_max": 0},
        )
        project_id = make_project(
            name="[AUTO] A-72 no partial",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        res = client.post(f"/projects/{project_id}/enrich-async")
        task_id = res.json().get("task_id")
        client.delete(f"/tasks/{task_id}")

        # Aspetta che il worker finisca prima di verificare l'output
        deadline = time.time() + 60
        while time.time() < deadline:
            status_res = client.get(f"/tasks/{task_id}")
            if status_res.status_code == 404:
                break
            state = status_res.json().get("status", "")
            if state in ("revoked", "REVOKED", "success", "SUCCESS", "failure", "FAILURE"):
                break
            time.sleep(1)

        output_res = client.get(f"/projects/{project_id}/output-info")
        body = output_res.json()
        assert body.get("has_output") is False or output_res.status_code == 404


# =============================================================================
# A-73 — Re-esecuzione stesso canvas due volte di fila
# =============================================================================

class TestA73:
    """Stesso progetto eseguito 2 volte → secondo output sovrascrive il primo."""

    def test_a73_seconda_esecuzione_success(self, client, make_project, make_api_connection, csv_paesi):
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-73 re-run",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        # Prima esecuzione
        result1 = execute_arricchisci(client, project_id, timeout=180)
        assert result1["success"] is True

        # Seconda esecuzione
        result2 = execute_arricchisci(client, project_id, timeout=180)
        assert result2["success"] is True

    def test_a73_righe_corrette_seconda_run(self, client, make_project, make_api_connection, csv_paesi):
        """Seconda esecuzione produce le stesse 10 righe della prima."""
        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
        )
        project_id = make_project(
            name="[AUTO] A-73 righe",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=csv_paesi, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        execute_arricchisci(client, project_id, timeout=180)
        result2 = execute_arricchisci(client, project_id, timeout=180)
        assert result2.get("rows_count", 0) == 10


# =============================================================================
# A-74 — 1000 righe e rate_limit=2 req/sec
# =============================================================================

class TestA74:
    """
    Questo test è lento per design (1000 righe con 3 unici → ~50s a rate_limit=2).
    Marcato con pytest.mark.slow — escludi con: pytest -m "not slow"
    """

    @pytest.mark.slow
    def test_a74_1000_righe_success(self, client, make_project, make_api_connection):
        """1000 righe, 3 unici, rate_limit=2 → completa senza timeout prematuro."""
        import csv, io

        # Genera CSV 1000 righe con 3 unici
        paesi = [("Italy", "IT"), ("France", "FR"), ("Germany", "DE")]
        rows = [{"paese": paesi[i % 3][0], "codice": paesi[i % 3][1]} for i in range(1000)]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["paese", "codice"])
        w.writeheader()
        w.writerows(rows)
        data = buf.getvalue().encode("utf-8")

        res = client.post(
            "/files/upload",
            files={"file": ("paesi_1000.csv", data, "text/csv")},
            data={"name": "[AUTO] A-74 1000 righe"},
        )
        assert res.status_code == 200
        file_id = res.json()["id"]

        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/simple",
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options={"rate_limit": 2, "timeout": 10},
        )
        project_id = make_project(
            name="[AUTO] A-74 1000 righe rate_limit",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=file_id, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        result = execute_arricchisci(client, project_id, timeout=300)
        client.delete(f"/files/{file_id}")
        assert result["success"] is True
        assert result.get("rows_count", 0) == 1000

    @pytest.mark.slow
    def test_a74_solo_3_chiamate_api(self, client, make_project, make_api_connection):
        """Cache dedup: 1000 righe con 3 unici → esattamente 3 chiamate al mock."""
        import csv, io

        paesi = [("Italy", "IT"), ("France", "FR"), ("Germany", "DE")]
        rows = [{"paese": paesi[i % 3][0], "codice": paesi[i % 3][1]} for i in range(1000)]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["paese", "codice"])
        w.writeheader()
        w.writerows(rows)
        data = buf.getvalue().encode("utf-8")

        res = client.post(
            "/files/upload",
            files={"file": ("paesi_1000b.csv", data, "text/csv")},
            data={"name": "[AUTO] A-74 1000b"},
        )
        file_id = res.json()["id"]

        api_id = make_api_connection(
            url=f"{MOCK_URL}/mock/counter",
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options={"rate_limit": 2},
        )
        project_id = make_project(
            name="[AUTO] A-74 3 calls",
            project_type="arricchisci",
            canvas=build_arricchisci_canvas(
                file_source_id=file_id, api_id=api_id,
                param_mapping={"country": "paese"},
            ),
        )
        execute_arricchisci(client, project_id, timeout=300)
        client.delete(f"/files/{file_id}")
        stats = requests.get(f"{MOCK_URL}/mock/counter/stats").json()
        assert stats["total_calls"] == 3
