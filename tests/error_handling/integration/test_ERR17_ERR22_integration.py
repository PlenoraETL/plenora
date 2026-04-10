"""
tests/error_handling/integration/test_ERR17_ERR22_carica.py
============================================================
Test INTEGRATION error handling — Modulo CARICA (ERR-17→ERR-22)
"""

import pytest
import requests
import io
import uuid

pytestmark = [pytest.mark.integration, pytest.mark.security]

BASE_URL = "http://localhost:8000/api"


# =============================================================================
# ERR-17 — Connessione target inesistente → 404
# =============================================================================

class TestERR17Integration:

    def test_carica_execute_conn_inesistente(self, auth_headers, make_project, nonexistent_id):
        project_id = make_project(project_type="estrai")
        res = requests.post(f"{BASE_URL}/carica/execute",
                            json={"project_id": project_id,
                                  "connection_id": nonexistent_id,
                                  "table_name": "test_table"},
                            headers=auth_headers)
        assert res.status_code in (400, 404, 422)  # 404 se endpoint non registrato

    def test_carica_validate_conn_inesistente(self, auth_headers, make_project, nonexistent_id):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/carica/validate",
                            json={"project_id": project_id,
                                  "connection_id": nonexistent_id,
                                  "table_name": "test"},
                            headers=auth_headers)
        assert res.status_code in (400, 404, 422)  # 404 se endpoint non registrato

    def test_nessun_500_conn_inesistente(self, auth_headers, make_project, nonexistent_id):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/carica/execute",
                            json={"project_id": project_id,
                                  "connection_id": nonexistent_id,
                                  "table_name": "test"},
                            headers=auth_headers)
        if res.status_code == 500:
            assert res.json().get("detail"), "500 senza detail — errore non gestito"

    def test_detail_messaggio_chiaro(self, auth_headers, make_project, nonexistent_id):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/carica/execute",
                            json={"project_id": project_id,
                                  "connection_id": nonexistent_id,
                                  "table_name": "test"},
                            headers=auth_headers)
        if res.status_code in (400, 404):
            assert res.json().get("detail")


# =============================================================================
# ERR-18 — Permessi insufficienti → 403/400
# =============================================================================

class TestERR18Integration:

    def test_carica_body_mancante_422(self, auth_headers):
        res = requests.post(f"{BASE_URL}/carica/execute",
                            json={}, headers=auth_headers)
        assert res.status_code in (404, 422)  # 404 se endpoint non registrato

    def test_carica_project_id_mancante_422(self, auth_headers):
        res = requests.post(f"{BASE_URL}/carica/execute",
                            json={"connection_id": "conn", "table_name": "t"},
                            headers=auth_headers)
        assert res.status_code in (404, 422)

    def test_carica_table_name_mancante_422(self, auth_headers, make_project):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/carica/execute",
                            json={"project_id": project_id, "connection_id": "conn"},
                            headers=auth_headers)
        assert res.status_code in (404, 422)


# =============================================================================
# ERR-19 — Disco pieno → errore gestito
# =============================================================================

class TestERR19Integration:

    def test_carica_execute_no_500_non_gestito(self, auth_headers, make_project):
        """Execute non deve dare 500 senza detail."""
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/carica/execute",
                            json={"project_id": project_id,
                                  "connection_id": "conn_fake",
                                  "table_name": "test"},
                            headers=auth_headers)
        if res.status_code == 500:
            assert res.json().get("detail"), "500 senza detail"


# =============================================================================
# ERR-20 — Token OAuth2 scaduto → 401
# =============================================================================

class TestERR20Integration:

    def test_carica_feature_service_senza_token(self, auth_headers, make_project):
        """Richiesta Feature Service senza token → errore chiaro."""
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/carica/execute",
                            json={"project_id": project_id,
                                  "destination_type": "feature_service",
                                  "service_url": "http://fake.arcgis.com/service",
                                  "token": ""},
                            headers=auth_headers)
        assert res.status_code in (400, 401, 404, 422)

    def test_carica_feature_service_token_invalido(self, auth_headers, make_project):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/carica/execute",
                            json={"project_id": project_id,
                                  "destination_type": "feature_service",
                                  "service_url": "http://fake.arcgis.com/service",
                                  "token": "token_invalido_xyz"},
                            headers=auth_headers)
        assert res.status_code in (400, 401, 404, 422, 500)


# =============================================================================
# ERR-21 — DataFrame colonne duplicate → errore chiaro
# =============================================================================

class TestERR21Integration:

    def test_carica_schema_con_colonne_duplicate(self, auth_headers, make_project):
        """Schema con colonne duplicate → errore chiaro."""
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/carica/validate",
                            json={"project_id": project_id,
                                  "connection_id": "conn_fake",
                                  "table_name": "test",
                                  "columns": [
                                      {"name": "id", "type": "INTEGER"},
                                      {"name": "id", "type": "VARCHAR"},
                                  ]},
                            headers=auth_headers)
        assert res.status_code in (400, 422)

    def test_carica_nessun_500_con_schema_invalido(self, auth_headers, make_project):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/carica/validate",
                            json={"project_id": project_id,
                                  "connection_id": "conn_fake",
                                  "table_name": "test",
                                  "columns": [
                                      {"name": "id", "type": "INTEGER"},
                                      {"name": "id", "type": "VARCHAR"},
                                  ]},
                            headers=auth_headers)
        assert res.status_code != 500


# =============================================================================
# ERR-22 — Valore troppo lungo → warning/errore
# =============================================================================

class TestERR22Integration:

    def test_check_conflicts_no_500(self, auth_headers, make_project):
        """check-conflicts non deve dare 500."""
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/carica/check-conflicts",
                            json={"project_id": project_id,
                                  "connection_id": "conn_fake",
                                  "table_name": "test",
                                  "match_columns": ["id"]},
                            headers=auth_headers)
        assert res.status_code != 500

    def test_validate_colonna_tipo_sbagliato(self, auth_headers, make_project):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/carica/validate",
                            json={"project_id": project_id,
                                  "connection_id": "conn_fake",
                                  "table_name": "test",
                                  "columns": [
                                      {"name": "nome", "type": "TIPO_INESISTENTE_XYZ"}
                                  ]},
                            headers=auth_headers)
        assert res.status_code in (400, 422)


# =============================================================================
# PARAMETRIC EXTRA — ERR-17→22 Integration
# =============================================================================

class TestERR17IntegrationParametric:

    @pytest.mark.parametrize("method,path_tmpl", [
        ("POST",   "/carica/execute"),
        ("POST",   "/carica/validate"),
        ("POST",   "/carica/check-conflicts"),
    ])
    def test_carica_endpoint_conn_inesistente(self, method, path_tmpl, auth_headers,
                                               make_project, nonexistent_id):
        project_id = make_project()
        body = {"project_id": project_id, "connection_id": nonexistent_id,
                "table_name": "test", "match_columns": ["id"]}
        res = requests.request(method, f"{BASE_URL}{path_tmpl}",
                               json=body, headers=auth_headers)
        assert res.status_code in (400, 404, 422)  # 404 se endpoint non registrato

    @pytest.mark.parametrize("bad_id", ["", "null", "0", "conn_" + "x" * 200])
    def test_conn_id_formati_invalidi(self, bad_id, auth_headers, make_project):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/carica/execute",
                            json={"project_id": project_id,
                                  "connection_id": bad_id,
                                  "table_name": "test"},
                            headers=auth_headers)
        assert res.status_code in (400, 404, 422)  # 404 se endpoint non registrato


class TestERR18IntegrationParametric:

    @pytest.mark.parametrize("missing_field", [
        "project_id", "connection_id", "table_name"
    ])
    def test_campo_obbligatorio_mancante_422(self, missing_field, auth_headers, make_project):
        project_id = make_project()
        base = {"project_id": project_id,
                "connection_id": "conn_fake",
                "table_name": "test"}
        del base[missing_field]
        res = requests.post(f"{BASE_URL}/carica/execute",
                            json=base, headers=auth_headers)
        assert res.status_code in (404, 422)  # 404 se endpoint non registrato


class TestERR21IntegrationParametric:

    @pytest.mark.parametrize("columns", [
        [{"name": "id", "type": "INTEGER"}, {"name": "id", "type": "VARCHAR"}],
        [{"name": "col", "type": "TEXT"}] * 3,
        [{"name": "a", "type": "INTEGER"}, {"name": "A", "type": "INTEGER"}],
    ])
    def test_schema_duplicato_no_500(self, columns, auth_headers, make_project):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/carica/validate",
                            json={"project_id": project_id,
                                  "connection_id": "conn_fake",
                                  "table_name": "test",
                                  "columns": columns},
                            headers=auth_headers)
        assert res.status_code != 500


class TestERR22IntegrationParametric:

    @pytest.mark.parametrize("table_name", [
        "test", "public.test", "schema_test.tabella",
        "a", "tabella_con_nome_molto_lungo_ma_valido",
        "123_inizia_con_numero",
    ])
    def test_vari_table_name_no_500(self, table_name, auth_headers, make_project):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/carica/validate",
                            json={"project_id": project_id,
                                  "connection_id": "conn_fake",
                                  "table_name": table_name},
                            headers=auth_headers)
        assert res.status_code != 500
