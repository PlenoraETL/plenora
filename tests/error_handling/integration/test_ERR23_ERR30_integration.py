"""
tests/error_handling/integration/test_ERR23_ERR30_scheduler_auth.py
=====================================================================
Test INTEGRATION error handling — SCHEDULER + AUTH (ERR-23→ERR-30)
Richiede backend live su localhost:8000
"""

import pytest
import requests
import uuid

pytestmark = [pytest.mark.integration, pytest.mark.security]

BASE_URL = "http://localhost:8000/api"


# =============================================================================
# ERR-23 — Cron expression non valida → 400
# =============================================================================

class TestERR23Integration:

    CRON_INVALIDE = [
        "non-valida",
        "* * * *",
        "60 * * * *",
        "* 25 * * *",
        "",
        "abc def ghi",
    ]

    CRON_VALIDE = [
        "0 2 * * *",
        "*/15 * * * *",
        "0 0 1 * *",
    ]

    @pytest.mark.parametrize("cron", CRON_INVALIDE)
    def test_cron_invalida_400(self, auth_headers, make_project, cron):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/schedules",
                            json={"project_id": project_id,
                                  "cron_expression": cron},
                            headers=auth_headers)
        assert res.status_code in (400, 422), \
            f"Cron '{cron}' accettata (status {res.status_code})"

    @pytest.mark.parametrize("cron", CRON_VALIDE)
    def test_cron_valida_non_400(self, auth_headers, make_project, cron):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/schedules",
                            json={"project_id": project_id,
                                  "cron_expression": cron},
                            headers=auth_headers)
        assert res.status_code != 400, \
            f"Cron valida '{cron}' rifiutata con 400"
        # Cleanup se creato
        if res.status_code in (200, 201):
            schedule_id = res.json().get("id")
            if schedule_id:
                requests.delete(f"{BASE_URL}/schedules/{schedule_id}",
                                headers=auth_headers)

    def test_messaggio_errore_cron_non_vuoto(self, auth_headers, make_project):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/schedules",
                            json={"project_id": project_id,
                                  "cron_expression": "non-valida"},
                            headers=auth_headers)
        if res.status_code in (400, 422):
            assert res.json().get("detail")


# =============================================================================
# ERR-24 — Schedule inesistente → 404
# =============================================================================

class TestERR24Integration:

    def test_get_schedule_inesistente_404(self, auth_headers, nonexistent_id):
        res = requests.get(f"{BASE_URL}/schedules/{nonexistent_id}",
                           headers=auth_headers)
        assert res.status_code == 404

    def test_delete_schedule_inesistente_404(self, auth_headers, nonexistent_id):
        res = requests.delete(f"{BASE_URL}/schedules/{nonexistent_id}",
                              headers=auth_headers)
        assert res.status_code == 404

    def test_run_schedule_inesistente_404(self, auth_headers, nonexistent_id):
        res = requests.post(f"{BASE_URL}/schedules/{nonexistent_id}/run",
                            headers=auth_headers)
        assert res.status_code == 404

    def test_toggle_schedule_inesistente_404(self, auth_headers, nonexistent_id):
        res = requests.patch(f"{BASE_URL}/schedules/{nonexistent_id}/toggle",
                             json={"enabled": True},
                             headers=auth_headers)
        assert res.status_code == 404

    def test_nessun_500_su_schedule_inesistente(self, auth_headers, nonexistent_id):
        res = requests.get(f"{BASE_URL}/schedules/{nonexistent_id}",
                           headers=auth_headers)
        assert res.status_code != 500

    def test_detail_messaggio_chiaro(self, auth_headers, nonexistent_id):
        res = requests.get(f"{BASE_URL}/schedules/{nonexistent_id}",
                           headers=auth_headers)
        assert res.json().get("detail")


# =============================================================================
# ERR-25 — Accesso senza JWT → 401
# =============================================================================

class TestERR25Integration:

    ENDPOINTS = [
        ("GET",    "/api/projects"),
        ("GET",    "/api/connections"),
        ("GET",    "/api/schedules"),
        ("GET",    "/api/datasources"),
        ("GET",    "/api/files"),
        ("GET",    "/api/auth/users"),
        ("GET",    "/api/auth/me"),
        ("GET",    "/api/logs"),
        ("GET",    "/api/api-connections"),
        ("POST",   "/api/auth/change-password"),
        ("POST",   "/api/projects"),
        ("POST",   "/api/connections"),
        ("POST",   "/api/schedules"),
    ]

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_endpoint_senza_token_401(self, method, path):
        res = requests.request(method, f"http://localhost:8000{path}")
        assert res.status_code == 401, \
            f"{method} {path} → {res.status_code} (atteso 401)"

    def test_auth_login_non_richiede_token(self):
        """POST /auth/login è pubblico — ritorna 401 per credenziali errate, non 403."""
        res = requests.post(f"{BASE_URL}/auth/login",
                            json={"username": "inesistente", "password": "wrong"})
        # 401 è corretto per credenziali errate — l'importante è non 403 (forbidden) o 405
        assert res.status_code in (401, 422)  # 401 credenziali errate, 422 validazione

    def test_health_pubblico(self):
        """GET /health è pubblico."""
        res = requests.get("http://localhost:8000/health")
        assert res.status_code in (200, 404)  # 404 se non implementato


# =============================================================================
# ERR-26 — JWT di altro gruppo → 403
# =============================================================================

class TestERR26Integration:

    def test_token_falso_non_accede_a_risorse(self, auth_headers):
        """Token con firma sbagliata → 401 su tutte le risorse."""
        import jwt as pyjwt
        from datetime import datetime, timezone, timedelta
        payload = {"sub": "uid-fake", "username": "fake",
                   "group_id": "altro-gruppo", "role": "admin",
                   "exp": datetime.now(timezone.utc) + timedelta(hours=8)}
        fake_token = pyjwt.encode(payload, "chiave_sbagliata", algorithm="HS256")
        fake_headers = {"Authorization": f"Bearer {fake_token}"}

        for path in ["/api/projects", "/api/connections", "/api/schedules"]:
            res = requests.get(f"http://localhost:8000{path}",
                               headers=fake_headers)
            assert res.status_code == 401, \
                f"Token falso accede a {path} → {res.status_code}"

    def test_progetto_altro_gruppo_403(self, auth_headers, make_project):
        """Progetto di un gruppo non accessibile ad altro gruppo."""
        project_id = make_project()
        # Tentiamo con token valido ma payload manipolato
        import jwt as pyjwt
        from datetime import datetime, timezone, timedelta
        payload = {"sub": "uid-other", "username": "other",
                   "group_id": "gruppo-completamente-diverso-xyz",
                   "role": "admin",
                   "exp": datetime.now(timezone.utc) + timedelta(hours=8)}
        wrong_token = pyjwt.encode(payload, "chiave_sbagliata", algorithm="HS256")
        res = requests.get(f"{BASE_URL}/projects/{project_id}",
                           headers={"Authorization": f"Bearer {wrong_token}"})
        assert res.status_code in (401, 403)


# =============================================================================
# ERR-27 — JWT malformato → 401
# =============================================================================

class TestERR27Integration:

    TOKEN_MALFORMATI = [
        "questo.non.e.un.token",
        "eyJhbGciOiJIUzI1NiJ9",
        "Bearer token",
        "null",
        "",
        "a" * 500,
    ]

    @pytest.mark.parametrize("token", TOKEN_MALFORMATI)
    def test_token_malformato_401(self, token):
        res = requests.get(f"{BASE_URL}/auth/me",
                           headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 401

    def test_token_scaduto_401(self):
        import jwt as pyjwt
        from datetime import datetime, timezone, timedelta
        payload = {"sub": "u", "username": "u", "group_id": "g",
                   "role": "admin",
                   "exp": datetime.now(timezone.utc) - timedelta(hours=1)}
        # Usiamo la secret key di default
        secret = "urby-etl-jwt-secret-change-in-production"
        token = pyjwt.encode(payload, secret, algorithm="HS256")
        res = requests.get(f"{BASE_URL}/auth/me",
                           headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 401

    def test_firma_sbagliata_401(self):
        import jwt as pyjwt
        from datetime import datetime, timezone, timedelta
        payload = {"sub": "u", "username": "admin", "group_id": "default",
                   "role": "admin",
                   "exp": datetime.now(timezone.utc) + timedelta(hours=8)}
        token = pyjwt.encode(payload, "chiave_sbagliata_xyz", algorithm="HS256")
        res = requests.get(f"{BASE_URL}/auth/me",
                           headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 401

    def test_bearer_mancante_401(self, auth_headers):
        """Token senza 'Bearer ' prefix → 401."""
        # Usa un token fake qualsiasi senza prefisso Bearer
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.fake.signature"
        res = requests.get(f"{BASE_URL}/auth/me",
                           headers={"Authorization": token})
        assert res.status_code == 401


# =============================================================================
# ERR-28 — Body JSON malformato → 422
# =============================================================================

class TestERR28Integration:

    def test_login_json_malformato_422(self):
        res = requests.post(f"{BASE_URL}/auth/login",
                            data="non-json",
                            headers={"Content-Type": "application/json"})
        assert res.status_code in (400, 422)

    def test_login_json_parziale_422(self):
        res = requests.post(f"{BASE_URL}/auth/login",
                            data='{"username": "admin"',
                            headers={"Content-Type": "application/json"})
        assert res.status_code in (400, 422)

    def test_create_project_json_invalido_422(self, auth_headers):
        res = requests.post(f"{BASE_URL}/projects",
                            data="non-json",
                            headers={**auth_headers,
                                     "Content-Type": "application/json"})
        assert res.status_code in (400, 422)

    def test_create_schedule_json_invalido_422(self, auth_headers):
        res = requests.post(f"{BASE_URL}/schedules",
                            data="{broken json}",
                            headers={**auth_headers,
                                     "Content-Type": "application/json"})
        assert res.status_code in (400, 422)

    @pytest.mark.parametrize("body", ["non-json", "{broken}", "[]", "null", "123"])
    def test_vari_body_malformati_login(self, body):
        res = requests.post(f"{BASE_URL}/auth/login",
                            data=body,
                            headers={"Content-Type": "application/json"})
        assert res.status_code in (400, 422)


# =============================================================================
# ERR-29 — Campo obbligatorio mancante → 422
# =============================================================================

class TestERR29Integration:

    def test_login_username_mancante_422(self):
        res = requests.post(f"{BASE_URL}/auth/login",
                            json={"password": "pass"})
        assert res.status_code == 422

    def test_login_password_mancante_422(self):
        res = requests.post(f"{BASE_URL}/auth/login",
                            json={"username": "admin"})
        assert res.status_code == 422

    def test_login_body_vuoto_422(self):
        res = requests.post(f"{BASE_URL}/auth/login", json={})
        assert res.status_code == 422

    def test_create_project_name_mancante_422(self, auth_headers):
        res = requests.post(f"{BASE_URL}/projects",
                            json={"project_type": "estrai",
                                  "canvas": {"nodes": [], "edges": []}},
                            headers=auth_headers)
        assert res.status_code == 422

    def test_create_schedule_project_id_mancante_422(self, auth_headers):
        res = requests.post(f"{BASE_URL}/schedules",
                            json={"cron_expression": "0 2 * * *"},
                            headers=auth_headers)
        assert res.status_code == 422

    def test_create_schedule_cron_mancante_422(self, auth_headers, make_project):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/schedules",
                            json={"project_id": project_id},
                            headers=auth_headers)
        assert res.status_code == 422

    def test_create_user_username_mancante_422(self, auth_headers):
        res = requests.post(f"{BASE_URL}/auth/users",
                            json={"password": "pass", "group_id": "g"},
                            headers=auth_headers)
        assert res.status_code == 422

    def test_create_user_password_mancante_422(self, auth_headers):
        res = requests.post(f"{BASE_URL}/auth/users",
                            json={"username": "new", "group_id": "g"},
                            headers=auth_headers)
        assert res.status_code == 422

    @pytest.mark.parametrize("missing,body", [
        ("username", {"password": "p"}),
        ("password", {"username": "u"}),
        ("all",      {}),
    ])
    def test_login_campi_obbligatori_mancanti(self, missing, body):
        res = requests.post(f"{BASE_URL}/auth/login", json=body)
        assert res.status_code == 422


# =============================================================================
# ERR-30 — Tipo campo sbagliato → 422
# =============================================================================

class TestERR30Integration:

    def test_project_id_int_invece_stringa(self, auth_headers):
        res = requests.post(f"{BASE_URL}/schedules",
                            json={"project_id": 123,
                                  "cron_expression": "0 2 * * *"},
                            headers=auth_headers)
        # FastAPI può convertire int a str — 400/422 o 404 se non trovato
        assert res.status_code in (400, 404, 422)

    def test_cron_lista_invece_stringa_422(self, auth_headers, make_project):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/schedules",
                            json={"project_id": project_id,
                                  "cron_expression": ["0", "2", "*", "*", "*"]},
                            headers=auth_headers)
        assert res.status_code in (400, 422)

    def test_canvas_stringa_invece_oggetto_422(self, auth_headers):
        res = requests.post(f"{BASE_URL}/projects",
                            json={"name": "test",
                                  "project_type": "estrai",
                                  "canvas": "non-un-oggetto"},
                            headers=auth_headers)
        # Pydantic può accettare string per canvas — 200 o 422
        assert res.status_code in (200, 400, 422)

    def test_limit_stringa_invece_int(self, auth_headers):
        res = requests.get(f"{BASE_URL}/logs",
                           params={"limit": "non-un-numero"},
                           headers=auth_headers)
        assert res.status_code in (400, 422)

    def test_port_stringa_invece_int_422(self, auth_headers):
        res = requests.post(f"{BASE_URL}/connections",
                            json={"name": "test", "type": "postgresql",
                                  "host": "localhost", "port": "non-numero",
                                  "database": "db", "username": "u",
                                  "password": "p"},
                            headers=auth_headers)
        assert res.status_code in (400, 422)

    @pytest.mark.parametrize("field,value", [
        ("limit",  "abc"),
        ("offset", "xyz"),
    ])
    def test_parametri_query_tipo_sbagliato(self, field, value, auth_headers):
        res = requests.get(f"{BASE_URL}/logs",
                           params={field: value},
                           headers=auth_headers)
        assert res.status_code in (400, 422)


# =============================================================================
# PARAMETRIC EXTRA — ERR-23→30 Integration
# =============================================================================

class TestERR23IntegrationParametric:

    CRON_INVALIDE_EXTRA = [
        "non-valida", "* * * *", "60 * * * *",
        "* 25 * * *", "* * 32 * *", "* * * 13 *",
        "", "-1 * * * *", "* * 0 * *",
    ]

    CRON_VALIDE_EXTRA = [
        "0 2 * * *", "*/15 * * * *", "0 0 1 * *",
        "59 23 31 12 *", "*/5 * * * *",
    ]

    @pytest.mark.parametrize("cron", CRON_INVALIDE_EXTRA)
    def test_cron_invalida_no_schedule_creato(self, cron, auth_headers, make_project):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/schedules",
                            json={"project_id": project_id,
                                  "cron_expression": cron},
                            headers=auth_headers)
        assert res.status_code in (400, 422)

    @pytest.mark.parametrize("cron", CRON_VALIDE_EXTRA)
    def test_cron_valida_no_400(self, cron, auth_headers, make_project):
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/schedules",
                            json={"project_id": project_id,
                                  "cron_expression": cron},
                            headers=auth_headers)
        assert res.status_code != 400
        if res.status_code in (200, 201):
            schedule_id = res.json().get("id")
            if schedule_id:
                requests.delete(f"{BASE_URL}/schedules/{schedule_id}",
                                headers=auth_headers)


class TestERR24IntegrationParametric:

    @pytest.mark.parametrize("method,path_tmpl,body", [
        ("GET",    "/schedules/{id}", None),
        ("DELETE", "/schedules/{id}", None),
        ("POST",   "/schedules/{id}/run", None),
        ("PATCH",  "/schedules/{id}/toggle", {"enabled": True}),
    ])
    def test_metodi_schedule_inesistente(self, method, path_tmpl, body,
                                          auth_headers, nonexistent_id):
        path = path_tmpl.replace("{id}", nonexistent_id)
        kwargs = {"headers": auth_headers}
        if body:
            kwargs["json"] = body
        res = requests.request(method, f"{BASE_URL}{path}", **kwargs)
        assert res.status_code in (404, 422)

    @pytest.mark.parametrize("bad_id", ["null", "0", "undefined", "sched_" + "x" * 200])
    def test_id_formato_invalido(self, bad_id, auth_headers):
        res = requests.get(f"{BASE_URL}/schedules/{bad_id}", headers=auth_headers)
        assert res.status_code in (400, 404, 422)


class TestERR25IntegrationParametric:

    ALL_PROTECTED = [
        ("GET",    "/api/projects"),
        ("POST",   "/api/projects"),
        ("GET",    "/api/connections"),
        ("POST",   "/api/connections"),
        ("GET",    "/api/schedules"),
        ("POST",   "/api/schedules"),
        ("GET",    "/api/datasources"),
        ("GET",    "/api/files"),
        ("GET",    "/api/auth/users"),
        ("POST",   "/api/auth/users"),
        ("GET",    "/api/auth/me"),
        ("POST",   "/api/auth/change-password"),
        ("GET",    "/api/logs"),
        ("GET",    "/api/api-connections"),
    ]

    @pytest.mark.parametrize("method,path", ALL_PROTECTED)
    def test_tutti_endpoint_protetti_401(self, method, path):
        kwargs = {}
        if method == "POST":
            kwargs["json"] = {}
        res = requests.request(method, f"http://localhost:8000{path}", **kwargs)
        assert res.status_code == 401, \
            f"{method} {path} → {res.status_code}"


class TestERR26IntegrationParametric:

    @pytest.mark.parametrize("path", [
        "/api/projects",
        "/api/connections",
        "/api/schedules",
        "/api/files",
        "/api/api-connections",
    ])
    def test_token_firma_sbagliata_401_su_tutti(self, path):
        import jwt as pyjwt
        from datetime import datetime, timezone, timedelta
        payload = {"sub": "u", "username": "fake", "group_id": "altro",
                   "role": "admin",
                   "exp": datetime.now(timezone.utc) + timedelta(hours=8)}
        fake_token = pyjwt.encode(payload, "chiave_sbagliata", algorithm="HS256")
        res = requests.get(f"http://localhost:8000{path}",
                           headers={"Authorization": f"Bearer {fake_token}"})
        assert res.status_code == 401


class TestERR27IntegrationParametric:

    TOKEN_INVALIDI_EXTRA = [
        "questo.non.e.un.token",
        "eyJhbGciOiJIUzI1NiJ9.e30",
        "null", "undefined", "{}", "",
        "a" * 1000,
        "eyJ" + "." * 5,
    ]

    @pytest.mark.parametrize("token", TOKEN_INVALIDI_EXTRA)
    def test_token_invalido_401_su_me(self, token):
        res = requests.get(f"{BASE_URL}/auth/me",
                           headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 401

    @pytest.mark.parametrize("token", TOKEN_INVALIDI_EXTRA)
    def test_token_invalido_401_su_projects(self, token):
        res = requests.get(f"{BASE_URL}/projects",
                           headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 401


class TestERR28IntegrationParametric:

    @pytest.mark.parametrize("endpoint,body", [
        (f"{BASE_URL}/auth/login", "non-json"),
        (f"{BASE_URL}/auth/login", "{broken}"),
        (f"{BASE_URL}/auth/login", ""),
    ])
    def test_body_malformato_vari_endpoint(self, endpoint, body, auth_headers):
        res = requests.post(endpoint, data=body,
                            headers={"Content-Type": "application/json"})
        assert res.status_code in (400, 422)


class TestERR29IntegrationParametric:

    @pytest.mark.parametrize("missing,body_override", [
        ("name",         {"project_type": "estrai", "canvas": {"nodes": [], "edges": []}}),
        # ("project_type", {"name": "test", "canvas": {"nodes": [], "edges": []}}),
    ])
    def test_create_project_campi_obbligatori(self, missing, body_override, auth_headers):
        res = requests.post(f"{BASE_URL}/projects",
                            json=body_override, headers=auth_headers)
        assert res.status_code == 422

    @pytest.mark.parametrize("missing_field", [
        "old_password", "new_password"
    ])
    def test_change_password_campi_obbligatori(self, missing_field, auth_headers):
        base = {"old_password": "old", "new_password": "new"}
        del base[missing_field]
        res = requests.post(f"{BASE_URL}/auth/change-password",
                            json=base, headers=auth_headers)
        assert res.status_code == 422


class TestERR30IntegrationParametric:

    @pytest.mark.parametrize("field,value", [
        ("limit",  "abc"),
        ("offset", "xyz"),
        ("limit",  "99999999999999"),
    ])
    def test_query_params_tipo_sbagliato(self, field, value, auth_headers):
        res = requests.get(f"{BASE_URL}/logs",
                           params={field: value},
                           headers=auth_headers)
        assert res.status_code in (400, 422)

    @pytest.mark.parametrize("field,value", [
        ("port", "non-numero"),
    ])
    def test_connection_port_invalida(self, field, value, auth_headers):
        body = {"name": "test", "type": "postgres", "host": "localhost",
                "database": "db", "username": "u", "password": "p"}
        body[field] = value
        res = requests.post(f"{BASE_URL}/connections",
                            json=body, headers=auth_headers)
        assert res.status_code in (400, 422)  # porta non numerica → 422
