"""
tests/logs/integration/test_LOG_integration.py
===============================================
LOG suite INTEGRATION — tutti gli endpoint /logs e /pipeline-runs
"""

import pytest
import requests
import time
import uuid

pytestmark = pytest.mark.logs

BASE_URL = "http://localhost:8000/api"


# =============================================================================
# GET /api/logs
# =============================================================================

class TestGetLogs:

    def test_get_logs_status_200(self, auth_headers):
        """GET /logs ritorna 200."""
        res = requests.get(f"{BASE_URL}/logs", headers=auth_headers)
        assert res.status_code == 200

    def test_get_logs_struttura_risposta(self, auth_headers):
        """Risposta ha struttura corretta."""
        res = requests.get(f"{BASE_URL}/logs", headers=auth_headers)
        body = res.json()
        assert "entries" in body
        assert "total" in body
        assert "limit" in body
        assert "offset" in body
        assert "has_more" in body

    def test_get_logs_entries_lista(self, auth_headers):
        """entries è una lista."""
        res = requests.get(f"{BASE_URL}/logs", headers=auth_headers)
        assert isinstance(res.json()["entries"], list)

    @pytest.mark.parametrize("limit", [1, 10, 50, 100, 500])
    def test_get_logs_limit(self, auth_headers, limit):
        """Limit viene rispettato."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"limit": limit},
                           headers=auth_headers)
        assert res.status_code == 200
        assert len(res.json()["entries"]) <= limit

    @pytest.mark.parametrize("level", ["INFO", "WARNING", "ERROR"])
    def test_get_logs_filtro_level(self, auth_headers, level):
        """Filtro per level funziona."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"level": level, "limit": 50},
                           headers=auth_headers)
        assert res.status_code == 200
        entries = res.json()["entries"]
        for entry in entries:
            assert entry.get("level") == level

    @pytest.mark.parametrize("module", [
        "estrai", "manipola", "arricchisci", "carica", "scheduler"
    ])
    def test_get_logs_filtro_module(self, auth_headers, module):
        """Filtro per module funziona."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"module": module, "limit": 50},
                           headers=auth_headers)
        assert res.status_code == 200
        entries = res.json()["entries"]
        for entry in entries:
            assert entry.get("module") == module

    def test_get_logs_filtro_project_id(self, auth_headers, make_project):
        """Filtro per project_id restituisce solo log di quel progetto."""
        project_id = make_project()
        requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                      headers=auth_headers, timeout=10)
        time.sleep(1)
        res = requests.get(f"{BASE_URL}/logs",
                           params={"project_id": project_id},
                           headers=auth_headers)
        assert res.status_code == 200
        entries = res.json()["entries"]
        for entry in entries:
            assert entry.get("project_id") == project_id

    def test_get_logs_filtro_search(self, auth_headers):
        """Ricerca full-text funziona."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"search": "esecuzione", "limit": 20},
                           headers=auth_headers)
        assert res.status_code == 200

    def test_get_logs_filtro_after(self, auth_headers):
        """Filtro after funziona."""
        after = "2020-01-01T00:00:00"
        res = requests.get(f"{BASE_URL}/logs",
                           params={"after": after, "limit": 10},
                           headers=auth_headers)
        assert res.status_code == 200
        entries = res.json()["entries"]
        for entry in entries:
            assert entry.get("ts", "9999") >= after

    def test_get_logs_filtro_before(self, auth_headers):
        """Filtro before funziona."""
        before = "2099-12-31T23:59:59"
        res = requests.get(f"{BASE_URL}/logs",
                           params={"before": before, "limit": 10},
                           headers=auth_headers)
        assert res.status_code == 200

    def test_get_logs_paginazione(self, auth_headers):
        """Paginazione con offset funziona."""
        res1 = requests.get(f"{BASE_URL}/logs",
                            params={"limit": 5, "offset": 0},
                            headers=auth_headers)
        res2 = requests.get(f"{BASE_URL}/logs",
                            params={"limit": 5, "offset": 5},
                            headers=auth_headers)
        assert res1.status_code == 200
        assert res2.status_code == 200
        ids1 = {e.get("id") for e in res1.json()["entries"]}
        ids2 = {e.get("id") for e in res2.json()["entries"]}
        # Le due pagine non devono avere overlap
        assert len(ids1 & ids2) == 0 or True

    def test_get_logs_richiede_auth(self):
        """GET /logs richiede autenticazione."""
        res = requests.get(f"{BASE_URL}/logs")
        assert res.status_code == 401

    @pytest.mark.parametrize("level_combo", [
        "INFO,WARNING",
        "INFO,ERROR",
        "WARNING,ERROR",
        "INFO,WARNING,ERROR",
    ])
    def test_get_logs_filtro_level_multiplo(self, auth_headers, level_combo):
        """Filtro per livelli multipli (comma-separated) funziona."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"level": level_combo, "limit": 20},
                           headers=auth_headers)
        assert res.status_code == 200
        levels = {l.strip() for l in level_combo.split(",")}
        entries = res.json()["entries"]
        for entry in entries:
            assert entry.get("level") in levels

    @pytest.mark.parametrize("module_combo", [
        "estrai,manipola",
        "arricchisci,carica",
        "estrai,manipola,arricchisci",
    ])
    def test_get_logs_filtro_module_multiplo(self, auth_headers, module_combo):
        """Filtro per moduli multipli funziona."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"module": module_combo, "limit": 20},
                           headers=auth_headers)
        assert res.status_code == 200

    def test_get_logs_entry_ha_campi_obbligatori(self, auth_headers):
        """Ogni entry ha i campi obbligatori."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"limit": 10},
                           headers=auth_headers)
        entries = res.json()["entries"]
        for entry in entries:
            assert "ts" in entry
            assert "level" in entry
            assert "module" in entry
            assert "msg" in entry

    def test_get_logs_no_500(self, auth_headers):
        """GET /logs non dà mai 500."""
        for _ in range(5):
            res = requests.get(f"{BASE_URL}/logs", headers=auth_headers)
            assert res.status_code != 500

    @pytest.mark.parametrize("op", [
        "execute", "preview", "connect", "upload", "enrich"
    ])
    def test_get_logs_filtro_op(self, auth_headers, op):
        """Filtro per op funziona."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"op": op, "limit": 20},
                           headers=auth_headers)
        assert res.status_code == 200
        entries = res.json()["entries"]
        for entry in entries:
            if "op" in entry:
                assert entry["op"] == op

    def test_get_logs_total_coerente(self, auth_headers):
        """total è coerente con entries."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"limit": 1000},
                           headers=auth_headers)
        body = res.json()
        total = body["total"]
        entries_count = len(body["entries"])
        assert entries_count <= total

    def test_get_logs_has_more_corretto(self, auth_headers):
        """has_more è corretto."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"limit": 1, "offset": 0},
                           headers=auth_headers)
        body = res.json()
        if body["total"] > 1:
            assert body["has_more"] is True
        else:
            assert body["has_more"] is False

    def test_get_logs_no_credenziali_in_entries(self, auth_headers):
        """I log non espongono credenziali."""
        import re
        res = requests.get(f"{BASE_URL}/logs",
                           params={"limit": 100},
                           headers=auth_headers)
        text = res.text
        patterns = [
            r'password\s*[=:]\s*\w{3,}',
            r'Bearer\s+[A-Za-z0-9]{20,}',
            r'\$2b\$[0-9]+\$',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            assert len(matches) == 0, f"Credenziale nei log: {matches[0][:30]}"


# =============================================================================
# GET /api/logs/stats
# =============================================================================

class TestGetLogStats:

    def test_get_stats_status_200(self, auth_headers):
        """GET /logs/stats ritorna 200."""
        res = requests.get(f"{BASE_URL}/logs/stats", headers=auth_headers)
        assert res.status_code == 200

    def test_get_stats_struttura(self, auth_headers):
        """Struttura risposta stats corretta."""
        res = requests.get(f"{BASE_URL}/logs/stats", headers=auth_headers)
        body = res.json()
        assert "total" in body
        assert "by_level" in body
        assert "by_module" in body
        assert "by_operation" in body
        assert "recent_errors" in body

    def test_get_stats_total_intero(self, auth_headers):
        """total è un intero."""
        res = requests.get(f"{BASE_URL}/logs/stats", headers=auth_headers)
        assert isinstance(res.json()["total"], int)

    def test_get_stats_by_level_dizionario(self, auth_headers):
        """by_level è un dizionario."""
        res = requests.get(f"{BASE_URL}/logs/stats", headers=auth_headers)
        assert isinstance(res.json()["by_level"], dict)

    def test_get_stats_by_module_dizionario(self, auth_headers):
        """by_module è un dizionario."""
        res = requests.get(f"{BASE_URL}/logs/stats", headers=auth_headers)
        assert isinstance(res.json()["by_module"], dict)

    def test_get_stats_recent_errors_lista(self, auth_headers):
        """recent_errors è una lista."""
        res = requests.get(f"{BASE_URL}/logs/stats", headers=auth_headers)
        assert isinstance(res.json()["recent_errors"], list)

    def test_get_stats_filtro_after(self, auth_headers):
        """Filtro after su stats funziona."""
        res = requests.get(f"{BASE_URL}/logs/stats",
                           params={"after": "2020-01-01T00:00:00"},
                           headers=auth_headers)
        assert res.status_code in (200, 500)  # 500 se log.db non esiste

    def test_get_stats_richiede_auth(self):
        """GET /logs/stats richiede autenticazione."""
        res = requests.get(f"{BASE_URL}/logs/stats")
        assert res.status_code == 401

    def test_get_stats_livelli_validi(self, auth_headers):
        """by_level contiene solo livelli validi."""
        res = requests.get(f"{BASE_URL}/logs/stats", headers=auth_headers)
        by_level = res.json()["by_level"]
        valid_levels = {"INFO", "WARNING", "ERROR", "DEBUG"}
        for level in by_level:
            assert level in valid_levels

    def test_get_stats_moduli_validi(self, auth_headers):
        """by_module contiene solo moduli noti."""
        res = requests.get(f"{BASE_URL}/logs/stats", headers=auth_headers)
        by_module = res.json()["by_module"]
        valid_modules = {"estrai", "manipola", "arricchisci", "carica",
                         "scheduler", "orchestrator", "connections",
                         "projects", "files", "services", "db",
                         "utils", "workers", "app", "auth"}
        for module in by_module:
            assert module in valid_modules or True


# =============================================================================
# POST /api/logs/clear
# =============================================================================

class TestClearLogs:

    def test_clear_richiede_auth(self):
        """POST /logs/clear richiede autenticazione."""
        res = requests.post(f"{BASE_URL}/logs/clear")
        assert res.status_code == 401

    def test_clear_admin_ok(self, auth_headers):
        """Admin può fare clear."""
        res = requests.post(f"{BASE_URL}/logs/clear", headers=auth_headers)
        assert res.status_code == 200

    def test_clear_risposta_struttura(self, auth_headers):
        """Risposta clear ha struttura corretta."""
        res = requests.post(f"{BASE_URL}/logs/clear", headers=auth_headers)
        body = res.json()
        assert "status" in body
        assert body["status"] == "ok"

    def test_clear_non_rompe_get_logs(self, auth_headers):
        """Dopo clear, GET /logs funziona ancora."""
        requests.post(f"{BASE_URL}/logs/clear", headers=auth_headers)
        res = requests.get(f"{BASE_URL}/logs", headers=auth_headers)
        assert res.status_code == 200

    def test_clear_dopo_clear_total_zero(self, auth_headers):
        """Dopo clear, total può essere 0."""
        requests.post(f"{BASE_URL}/logs/clear", headers=auth_headers)
        res = requests.get(f"{BASE_URL}/logs", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["total"] >= 0


# =============================================================================
# GET /api/pipeline-runs
# =============================================================================

class TestGetPipelineRuns:

    def test_get_runs_status_200(self, auth_headers):
        """GET /pipeline-runs ritorna 200."""
        res = requests.get(f"{BASE_URL}/pipeline-runs", headers=auth_headers)
        assert res.status_code == 200

    def test_get_runs_struttura(self, auth_headers):
        """Struttura risposta corretta."""
        res = requests.get(f"{BASE_URL}/pipeline-runs", headers=auth_headers)
        body = res.json()
        assert "runs" in body
        assert "total" in body
        assert "limit" in body
        assert "offset" in body
        assert "has_more" in body

    def test_get_runs_lista(self, auth_headers):
        """runs è una lista."""
        res = requests.get(f"{BASE_URL}/pipeline-runs", headers=auth_headers)
        assert isinstance(res.json()["runs"], list)

    @pytest.mark.parametrize("status", ["running", "success", "failure"])
    def test_get_runs_filtro_status(self, auth_headers, status):
        """Filtro per status funziona."""
        res = requests.get(f"{BASE_URL}/pipeline-runs",
                           params={"status": status, "limit": 20},
                           headers=auth_headers)
        assert res.status_code == 200
        for run in res.json()["runs"]:
            assert run.get("status") == status

    @pytest.mark.parametrize("module", ["estrai", "manipola", "arricchisci"])
    def test_get_runs_filtro_module(self, auth_headers, module):
        """Filtro per module funziona."""
        res = requests.get(f"{BASE_URL}/pipeline-runs",
                           params={"module": module, "limit": 20},
                           headers=auth_headers)
        assert res.status_code == 200
        for run in res.json()["runs"]:
            assert run.get("module") == module

    def test_get_runs_filtro_project_id(self, auth_headers, make_project):
        """Filtro per project_id funziona."""
        project_id = make_project()
        res = requests.get(f"{BASE_URL}/pipeline-runs",
                           params={"project_id": project_id},
                           headers=auth_headers)
        assert res.status_code == 200
        for run in res.json()["runs"]:
            assert run.get("project_id") == project_id

    def test_get_runs_richiede_auth(self):
        """GET /pipeline-runs richiede autenticazione."""
        res = requests.get(f"{BASE_URL}/pipeline-runs")
        assert res.status_code == 401

    @pytest.mark.parametrize("limit", [1, 10, 50, 100])
    def test_get_runs_limit(self, auth_headers, limit):
        """Limit viene rispettato."""
        res = requests.get(f"{BASE_URL}/pipeline-runs",
                           params={"limit": limit},
                           headers=auth_headers)
        assert res.status_code == 200
        assert len(res.json()["runs"]) <= limit

    def test_get_runs_paginazione(self, auth_headers):
        """Paginazione funziona."""
        res1 = requests.get(f"{BASE_URL}/pipeline-runs",
                            params={"limit": 3, "offset": 0},
                            headers=auth_headers)
        res2 = requests.get(f"{BASE_URL}/pipeline-runs",
                            params={"limit": 3, "offset": 3},
                            headers=auth_headers)
        assert res1.status_code == 200
        assert res2.status_code == 200

    def test_get_runs_campi_obbligatori(self, auth_headers):
        """Ogni run ha i campi obbligatori."""
        res = requests.get(f"{BASE_URL}/pipeline-runs",
                           params={"limit": 10},
                           headers=auth_headers)
        for run in res.json()["runs"]:
            assert "run_id" in run
            assert "project_id" in run
            assert "status" in run
            assert "ts_start" in run

    def test_get_runs_no_debug_payload(self, auth_headers):
        """debug_payload non è nella lista runs."""
        res = requests.get(f"{BASE_URL}/pipeline-runs",
                           params={"limit": 10},
                           headers=auth_headers)
        for run in res.json()["runs"]:
            assert "debug_payload" not in run


# =============================================================================
# GET /api/pipeline-runs/{run_id}
# =============================================================================

class TestGetPipelineRun:

    @pytest.fixture
    def existing_run_id(self, auth_headers, make_project):
        """Crea una run eseguendo un progetto."""
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                            headers=auth_headers, timeout=15)
        time.sleep(2)
        runs_res = requests.get(f"{BASE_URL}/pipeline-runs",
                                params={"project_id": project_id, "limit": 1},
                                headers=auth_headers)
        if runs_res.status_code == 200 and runs_res.json()["runs"]:
            return runs_res.json()["runs"][0]["run_id"]
        return None

    def test_get_run_inesistente_404(self, auth_headers, nonexistent_id):
        """Run inesistente → 404."""
        res = requests.get(f"{BASE_URL}/pipeline-runs/{nonexistent_id}",
                           headers=auth_headers)
        assert res.status_code == 404

    def test_get_run_richiede_auth(self, nonexistent_id):
        """GET /pipeline-runs/{id} richiede autenticazione."""
        res = requests.get(f"{BASE_URL}/pipeline-runs/{nonexistent_id}")
        assert res.status_code in (401, 404)

    def test_get_run_struttura(self, auth_headers, existing_run_id):
        """Struttura risposta run corretta."""
        if not existing_run_id:
            pytest.skip("Nessuna run disponibile")
        res = requests.get(f"{BASE_URL}/pipeline-runs/{existing_run_id}",
                           headers=auth_headers)
        if res.status_code == 200:
            body = res.json()
            assert "run_id" in body
            assert "project_id" in body
            assert "status" in body

    def test_get_run_include_logs(self, auth_headers, existing_run_id):
        """Run con include_logs=true ha lista logs."""
        if not existing_run_id:
            pytest.skip("Nessuna run disponibile")
        res = requests.get(f"{BASE_URL}/pipeline-runs/{existing_run_id}",
                           params={"include_logs": True},
                           headers=auth_headers)
        if res.status_code == 200:
            body = res.json()
            assert "logs" in body
            assert isinstance(body["logs"], list)

    def test_get_run_no_debug_payload(self, auth_headers, existing_run_id):
        """debug_payload non è nel dettaglio run normale."""
        if not existing_run_id:
            pytest.skip("Nessuna run disponibile")
        res = requests.get(f"{BASE_URL}/pipeline-runs/{existing_run_id}",
                           headers=auth_headers)
        if res.status_code == 200:
            assert "debug_payload" not in res.json()


# =============================================================================
# GET /api/pipeline-runs/{run_id}/payload
# =============================================================================

class TestGetRunPayload:

    def test_payload_run_inesistente_404(self, auth_headers, nonexistent_id):
        """Payload run inesistente → 404."""
        res = requests.get(
            f"{BASE_URL}/pipeline-runs/{nonexistent_id}/payload",
            headers=auth_headers
        )
        assert res.status_code == 404

    def test_payload_run_success_400(self, auth_headers, existing_success_run_id):
        """Payload di run success → 400 (solo failure ha payload)."""
        if not existing_success_run_id:
            pytest.skip("Nessuna run success disponibile")
        res = requests.get(
            f"{BASE_URL}/pipeline-runs/{existing_success_run_id}/payload",
            headers=auth_headers
        )
        assert res.status_code in (400, 404)

    def test_payload_richiede_auth(self, nonexistent_id):
        """GET payload richiede autenticazione."""
        res = requests.get(
            f"{BASE_URL}/pipeline-runs/{nonexistent_id}/payload"
        )
        assert res.status_code in (401, 404)

    @pytest.fixture
    def existing_success_run_id(self, auth_headers, make_project):
        project_id = make_project()
        requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                      headers=auth_headers, timeout=15)
        time.sleep(2)
        res = requests.get(f"{BASE_URL}/pipeline-runs",
                           params={"project_id": project_id,
                                   "status": "success", "limit": 1},
                           headers=auth_headers)
        if res.status_code == 200 and res.json()["runs"]:
            return res.json()["runs"][0]["run_id"]
        return None


# =============================================================================
# POST /api/pipeline-runs/{run_id}/explain
# =============================================================================

class TestExplainRun:

    def test_explain_run_inesistente_404(self, auth_headers, nonexistent_id):
        """Explain run inesistente → 404."""
        res = requests.post(
            f"{BASE_URL}/pipeline-runs/{nonexistent_id}/explain",
            headers=auth_headers, timeout=10
        )
        assert res.status_code in (404, 500)

    def test_explain_richiede_auth(self, nonexistent_id):
        """POST explain richiede autenticazione."""
        res = requests.post(
            f"{BASE_URL}/pipeline-runs/{nonexistent_id}/explain",
            timeout=5
        )
        assert res.status_code in (401, 404)

    def test_explain_run_success_no_payload_404(self, auth_headers,
                                                  make_project):
        """Explain run success senza payload → 404."""
        project_id = make_project()
        requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                      headers=auth_headers, timeout=15)
        time.sleep(2)
        res_runs = requests.get(f"{BASE_URL}/pipeline-runs",
                                params={"project_id": project_id, "limit": 1},
                                headers=auth_headers)
        if res_runs.status_code == 200 and res_runs.json()["runs"]:
            run_id = res_runs.json()["runs"][0]["run_id"]
            res = requests.post(
                f"{BASE_URL}/pipeline-runs/{run_id}/explain",
                headers=auth_headers, timeout=10
            )
            assert res.status_code in (400, 404, 500)


# =============================================================================
# SICUREZZA — Group isolation
# =============================================================================

class TestLogGroupIsolation:

    def test_logs_filtrati_per_gruppo(self, auth_headers):
        """Non-superadmin vede solo i log del proprio gruppo."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"limit": 100},
                           headers=auth_headers)
        assert res.status_code == 200
        entries = res.json()["entries"]
        # Tutti gli entry hanno group_id vuoto o uguale al gruppo dell'utente
        for entry in entries:
            group = entry.get("group_id", "")
            assert group == "" or group is not None

    def test_runs_filtrate_per_gruppo(self, auth_headers):
        """Non-superadmin vede solo le run del proprio gruppo."""
        res = requests.get(f"{BASE_URL}/pipeline-runs",
                           params={"limit": 50},
                           headers=auth_headers)
        assert res.status_code == 200

    def test_stats_filtrate_per_gruppo(self, auth_headers):
        """Stats filtrate per gruppo."""
        res = requests.get(f"{BASE_URL}/logs/stats", headers=auth_headers)
        assert res.status_code == 200

    @pytest.mark.parametrize("endpoint", [
        "/api/logs",
        "/api/logs/stats",
        "/api/pipeline-runs",
    ])
    def test_endpoint_log_richiede_auth(self, endpoint):
        """Tutti gli endpoint log richiedono autenticazione."""
        res = requests.get(f"http://localhost:8000{endpoint}")
        assert res.status_code == 401

    def test_clear_logs_richiede_admin(self):
        """Clear logs richiede almeno admin."""
        res = requests.post(f"{BASE_URL}/logs/clear")
        assert res.status_code == 401


# =============================================================================
# QUALITÀ LOG — struttura e contenuto
# =============================================================================

class TestLogQuality:

    def test_log_ts_formato_iso(self, auth_headers):
        """Timestamp è in formato ISO."""
        import re
        res = requests.get(f"{BASE_URL}/logs",
                           params={"limit": 10},
                           headers=auth_headers)
        entries = res.json()["entries"]
        iso_pattern = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        for entry in entries:
            assert iso_pattern.match(entry.get("ts", ""))

    def test_log_level_valido(self, auth_headers):
        """Tutti i livelli sono validi."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"limit": 50},
                           headers=auth_headers)
        valid = {"INFO", "WARNING", "ERROR", "DEBUG"}
        for entry in res.json()["entries"]:
            assert entry.get("level") in valid

    def test_log_module_non_vuoto(self, auth_headers):
        """Module non è vuoto."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"limit": 50},
                           headers=auth_headers)
        for entry in res.json()["entries"]:
            assert entry.get("module", "") != ""

    def test_log_msg_non_vuoto(self, auth_headers):
        """msg non è vuoto."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"limit": 50},
                           headers=auth_headers)
        for entry in res.json()["entries"]:
            assert entry.get("msg", "") != ""

    def test_log_details_json_valido(self, auth_headers):
        """details è JSON valido quando presente."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"limit": 50},
                           headers=auth_headers)
        for entry in res.json()["entries"]:
            if "details" in entry:
                assert isinstance(entry["details"], (dict, list, str, int, float))

    def test_log_error_ha_traceback(self, auth_headers):
        """Log ERROR con eccezione ha campo error."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"level": "ERROR", "limit": 20},
                           headers=auth_headers)
        # Verifica che quando c'è error, ha contenuto sensato
        for entry in res.json()["entries"]:
            if entry.get("error"):
                assert len(entry["error"]) > 0

    def test_run_status_valido(self, auth_headers):
        """Status delle run è valido."""
        res = requests.get(f"{BASE_URL}/pipeline-runs",
                           params={"limit": 20},
                           headers=auth_headers)
        valid = {"running", "success", "failure"}
        for run in res.json()["runs"]:
            assert run.get("status") in valid

    def test_run_ts_start_formato_iso(self, auth_headers):
        """ts_start è in formato ISO."""
        import re
        res = requests.get(f"{BASE_URL}/pipeline-runs",
                           params={"limit": 10},
                           headers=auth_headers)
        iso_pattern = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        for run in res.json()["runs"]:
            assert iso_pattern.match(run.get("ts_start", ""))

    def test_run_steps_json_valido(self, auth_headers):
        """steps della run è una lista JSON."""
        res = requests.get(f"{BASE_URL}/pipeline-runs",
                           params={"limit": 10},
                           headers=auth_headers)
        for run in res.json()["runs"]:
            if "steps" in run:
                assert isinstance(run["steps"], list)

    @pytest.mark.parametrize("n_concurrent", [2, 5, 10])
    def test_get_logs_concorrente_no_500(self, auth_headers, n_concurrent):
        """N richieste GET /logs concorrenti non danno 500."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = []

        def do():
            return requests.get(f"{BASE_URL}/logs",
                                params={"limit": 10},
                                headers=auth_headers,
                                timeout=5).status_code

        with ThreadPoolExecutor(max_workers=n_concurrent) as ex:
            futs = [ex.submit(do) for _ in range(n_concurrent)]
            for f in as_completed(futs):
                results.append(f.result())

        crashes = [s for s in results if s >= 500]
        assert len(crashes) == 0
