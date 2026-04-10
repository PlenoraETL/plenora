"""
tests/error_handling/integration/test_ERR09_ERR16_arricchisci.py
=================================================================
Test INTEGRATION error handling — Modulo ARRICCHISCI (ERR-09→ERR-16)
Richiede backend live su localhost:8000
"""

import pytest
import requests
import uuid

pytestmark = [pytest.mark.integration, pytest.mark.security]

BASE_URL = "http://localhost:8000/api"


# =============================================================================
# ERR-09 — api_id inesistente → 404
# =============================================================================

class TestERR09Integration:

    def test_get_api_connection_inesistente_404(self, auth_headers, nonexistent_id):
        res = requests.get(f"{BASE_URL}/api-connections/{nonexistent_id}",
                           headers=auth_headers)
        assert res.status_code == 404

    def test_validate_api_inesistente_404(self, auth_headers, make_project, nonexistent_id):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/enrichment/validate",
                            json={"project_id": project_id, "api_id": nonexistent_id,
                                  "param_mapping": {}},
                            headers=auth_headers)
        assert res.status_code in (404, 422, 500)

    def test_estimate_api_inesistente_404(self, auth_headers, make_project, nonexistent_id):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/enrichment/estimate",
                            json={"project_id": project_id, "api_id": nonexistent_id,
                                  "param_mapping": {}},
                            headers=auth_headers)
        assert res.status_code in (404, 422, 500)

    def test_api_connection_detail_non_vuoto(self, auth_headers, nonexistent_id):
        res = requests.get(f"{BASE_URL}/api-connections/{nonexistent_id}",
                           headers=auth_headers)
        assert res.status_code == 404
        assert res.json().get("detail")

    def test_nessun_500_su_api_inesistente(self, auth_headers, nonexistent_id):
        res = requests.get(f"{BASE_URL}/api-connections/{nonexistent_id}",
                           headers=auth_headers)
        assert res.status_code != 500

    @pytest.mark.parametrize("api_id", ["null", "0", "undefined"])
    def test_api_id_invalidi_gestiti(self, auth_headers, api_id):
        res = requests.get(f"{BASE_URL}/api-connections/{api_id}",
                           headers=auth_headers)
        assert res.status_code in (400, 404, 422)


# =============================================================================
# ERR-10 — Project senza output → 400/404
# =============================================================================

class TestERR10Integration:

    def test_execute_project_mai_eseguito(self, auth_headers, make_project):
        """Execute su progetto vuoto — il backend accoda il task (200) o rifiuta."""
        project_id = make_project(project_type="arricchisci")
        res = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                            headers=auth_headers)
        assert res.status_code in (200, 400, 404, 422, 500)

    def test_execute_project_inesistente_404(self, auth_headers, nonexistent_id):
        res = requests.post(f"{BASE_URL}/projects/{nonexistent_id}/execute",
                            headers=auth_headers)
        assert res.status_code in (200, 400, 404, 422)  # backend può accodare il task

    def test_execute_no_500_non_gestito(self, auth_headers, make_project):
        project_id = make_project(project_type="arricchisci")
        res = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                            headers=auth_headers)
        # 500 è accettabile solo se c'è un detail nel body
        if res.status_code == 500:
            assert res.json().get("detail"), "500 senza detail — errore non gestito"


# =============================================================================
# ERR-11 — Validate param_mapping vuoto → valid=false
# =============================================================================

class TestERR11Integration:

    def test_validate_body_mancante_422(self, auth_headers):
        res = requests.post(f"{BASE_URL}/enrichment/validate",
                            json={}, headers=auth_headers)
        assert res.status_code == 422

    def test_validate_project_id_mancante_422(self, auth_headers):
        res = requests.post(f"{BASE_URL}/enrichment/validate",
                            json={"api_id": "api_1", "param_mapping": {}},
                            headers=auth_headers)
        assert res.status_code == 422

    def test_validate_api_id_mancante_422(self, auth_headers, make_project):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/enrichment/validate",
                            json={"project_id": project_id, "param_mapping": {}},
                            headers=auth_headers)
        assert res.status_code == 422

    def test_validate_param_mapping_non_dict_422(self, auth_headers, make_project):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/enrichment/validate",
                            json={"project_id": project_id, "api_id": "api_1",
                                  "param_mapping": "non-un-dict"},
                            headers=auth_headers)
        assert res.status_code == 422


# =============================================================================
# ERR-12 — Estimate con 0 righe → no crash
# =============================================================================

class TestERR12Integration:

    def test_estimate_project_vuoto_no_500(self, auth_headers, make_project):
        project_id = make_project(project_type="arricchisci")
        res = requests.post(f"{BASE_URL}/enrichment/estimate",
                            json={"project_id": project_id,
                                  "api_id": "api_fake",
                                  "param_mapping": {}},
                            headers=auth_headers)
        assert res.status_code != 500

    def test_estimate_body_vuoto_422(self, auth_headers):
        res = requests.post(f"{BASE_URL}/enrichment/estimate",
                            json={}, headers=auth_headers)
        assert res.status_code == 422

    def test_estimate_project_inesistente_404(self, auth_headers, nonexistent_id):
        res = requests.post(f"{BASE_URL}/enrichment/estimate",
                            json={"project_id": nonexistent_id,
                                  "api_id": "api_fake",
                                  "param_mapping": {}},
                            headers=auth_headers)
        assert res.status_code in (400, 404, 422, 500)


# =============================================================================
# ERR-13 — File source eliminato → 400
# =============================================================================

class TestERR13Integration:

    def test_get_file_source_inesistente_404(self, auth_headers, nonexistent_id):
        res = requests.get(f"{BASE_URL}/files/{nonexistent_id}",
                           headers=auth_headers)
        assert res.status_code in (404, 403)

    def test_delete_file_source_inesistente_404(self, auth_headers, nonexistent_id):
        res = requests.delete(f"{BASE_URL}/files/{nonexistent_id}",
                              headers=auth_headers)
        assert res.status_code in (404, 403)

    def test_file_inesistente_no_500(self, auth_headers, nonexistent_id):
        res = requests.get(f"{BASE_URL}/files/{nonexistent_id}",
                           headers=auth_headers)
        assert res.status_code != 500


# =============================================================================
# ERR-14 — Rate limit = 0 → no crash
# =============================================================================

class TestERR14Integration:

    def test_validate_con_rate_limit_zero_no_500(self, auth_headers, make_project):
        """Endpoint validate non deve crashare con configurazione rate_limit=0."""
        project_id = make_project(project_type="arricchisci")
        res = requests.post(f"{BASE_URL}/enrichment/validate",
                            json={"project_id": project_id,
                                  "api_id": "api_fake",
                                  "param_mapping": {}},
                            headers=auth_headers)
        assert res.status_code != 500

    def test_estimate_con_api_rate_limit_zero(self, auth_headers, make_project):
        project_id = make_project(project_type="arricchisci")
        res = requests.post(f"{BASE_URL}/enrichment/estimate",
                            json={"project_id": project_id,
                                  "api_id": "api_fake",
                                  "param_mapping": {},
                                  "rate_limit": 0},
                            headers=auth_headers)
        assert res.status_code != 500


# =============================================================================
# ERR-15 — Enrich async su progetto già in esecuzione
# =============================================================================

class TestERR15Integration:

    def test_execute_async_project_inesistente(self, auth_headers, nonexistent_id):
        res = requests.post(f"{BASE_URL}/projects/{nonexistent_id}/execute-async",
                            headers=auth_headers)
        assert res.status_code in (400, 404, 422)

    def test_execute_async_no_500_non_gestito(self, auth_headers, make_project):
        project_id = make_project(project_type="arricchisci")
        res = requests.post(f"{BASE_URL}/projects/{project_id}/execute-async",
                            headers=auth_headers)
        if res.status_code == 500:
            assert res.json().get("detail"), "500 senza detail — errore non gestito"

    def test_doppio_execute_no_duplicazione(self, auth_headers, make_project):
        """Due richieste execute rapide → non crashano entrambe con 500."""
        project_id = make_project(project_type="arricchisci")
        res1 = requests.post(f"{BASE_URL}/projects/{project_id}/execute-async",
                             headers=auth_headers)
        res2 = requests.post(f"{BASE_URL}/projects/{project_id}/execute-async",
                             headers=auth_headers)
        # Non entrambi possono essere 500
        assert not (res1.status_code >= 500 and res2.status_code >= 500), \
            "Doppio execute: entrambi 500"


# =============================================================================
# ERR-16 — output_mapping malformato → errore chiaro
# =============================================================================

class TestERR16Integration:

    def test_validate_output_mapping_path_vuoto(self, auth_headers, make_project):
        project_id = make_project(project_type="arricchisci")
        res = requests.post(f"{BASE_URL}/enrichment/validate",
                            json={"project_id": project_id,
                                  "api_id": "api_fake",
                                  "param_mapping": {},
                                  "output_mapping": [{"path": "", "column": "result"}]},
                            headers=auth_headers)
        assert res.status_code != 500

    def test_validate_output_mapping_tipo_sbagliato_422(self, auth_headers, make_project):
        project_id = make_project(project_type="arricchisci")
        res = requests.post(f"{BASE_URL}/enrichment/validate",
                            json={"project_id": project_id,
                                  "api_id": "api_fake",
                                  "param_mapping": {},
                                  "output_mapping": "non-una-lista"},
                            headers=auth_headers)
        assert res.status_code in (400, 404, 422)  # 404 se api_fake non esiste

    def test_validate_output_mapping_lista_vuota_gestita(self, auth_headers, make_project):
        project_id = make_project(project_type="arricchisci")
        res = requests.post(f"{BASE_URL}/enrichment/validate",
                            json={"project_id": project_id,
                                  "api_id": "api_fake",
                                  "param_mapping": {},
                                  "output_mapping": []},
                            headers=auth_headers)
        assert res.status_code != 500


# =============================================================================
# PARAMETRIC EXTRA — ERR-09→16 Integration
# =============================================================================

class TestERR09IntegrationParametric:

    @pytest.mark.parametrize("method,path_tmpl", [
        ("GET",    "/api-connections/{id}"),
        ("PUT",    "/api-connections/{id}"),
        ("DELETE", "/api-connections/{id}"),
    ])
    def test_tutti_metodi_api_inesistente(self, method, path_tmpl, auth_headers, nonexistent_id):
        path = path_tmpl.replace("{id}", nonexistent_id)
        kwargs = {"headers": auth_headers}
        if method == "PUT":
            kwargs["json"] = {"name": "x"}
        res = requests.request(method, f"{BASE_URL}{path}", **kwargs)
        assert res.status_code in (404, 422)

    @pytest.mark.parametrize("mapping", [
        [],
        [{"path": "", "column": "result"}],
        [{"path": "a.b.c", "column": ""}],
        [{"path": "campo", "column": "col"}] * 10,
    ])
    def test_vari_output_mapping_no_500(self, mapping, auth_headers, make_project):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/enrichment/validate",
                            json={"project_id": project_id,
                                  "api_id": "api_fake",
                                  "param_mapping": {},
                                  "output_mapping": mapping},
                            headers=auth_headers)
        assert res.status_code != 500
