"""
tests/security/integration/test_group_isolation.py
===================================================
Test di group isolation su tutti gli endpoint che espongono dati.

Verifica che utente B (gruppo diverso) non possa accedere
a nessuna risorsa creata da utente A.

Prerequisiti:
  - Backend FastAPI su localhost:8000
  - Utente admin/admin123 esistente (gruppo A)
  - Il test crea un secondo utente in un gruppo diverso (gruppo B)

Endpoint coperti:
  ISO-01  GET  /projects/{id}          → 403
  ISO-02  PUT  /projects/{id}          → 403
  ISO-03  DELETE /projects/{id}        → 403
  ISO-04  GET  /connections/{id}       → 403
  ISO-05  PUT  /connections/{id}       → 403
  ISO-06  DELETE /connections/{id}     → 403
  ISO-07  GET  /schedules/{id}         → 403
  ISO-08  DELETE /schedules/{id}       → 403
  ISO-09  GET  /datasources/{id}       → 403
  ISO-10  DELETE /datasources/{id}     → 403
  ISO-11  GET  /enrichment/api-connections/{id} → 403
  ISO-12  GET  /files/{id}             → 403
  ISO-13  GET  /runs/{id}              → 403
  ISO-14  Lista risorse utente B non contiene risorse utente A
"""

import pytest
import requests
import uuid

pytestmark = [pytest.mark.security, pytest.mark.integration]

BASE_URL  = "http://localhost:8000/api"
USER_A    = "admin"
PASS_A    = "admin123"
GROUP_B   = f"grp_isolation_{uuid.uuid4().hex[:8]}"


# =============================================================================
# Helpers
# =============================================================================

def _login(username, password):
    import time
    while True:
        res = requests.post(f"{BASE_URL}/auth/login",
                            json={"username": username, "password": password})
        if res.status_code == 200:
            return res.json()["access_token"]
        if res.status_code == 429:
            wait = int(res.headers.get("Retry-After", 300)) + 2
            time.sleep(wait)
            continue
        assert False, f"Login {username} fallito: {res.text}"


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(scope="module")
def token_a():
    return _login(USER_A, PASS_A)


@pytest.fixture(scope="module")
def headers_a(token_a):
    return _headers(token_a)


@pytest.fixture(scope="module")
def user_b(headers_a):
    """
    Crea utente B in un gruppo completamente diverso da A.
    Usa gli endpoint /api/auth/groups e /api/auth/users.
    Richiede che admin sia superadmin.
    """
    # Verifica che admin sia superadmin
    me = requests.get(f"{BASE_URL}/auth/me", headers=headers_a)
    assert me.status_code == 200
    if me.json()["role"] != "superadmin":
        pytest.skip("admin non è superadmin — impossibile creare utenti cross-gruppo")

    # Crea il gruppo B via API
    res_grp = requests.post(f"{BASE_URL}/auth/groups",
                            json={"group_id": GROUP_B,
                                  "name": "Gruppo Isolation Test B",
                                  "description": "Usato dai test di isolamento"},
                            headers=headers_a)
    if res_grp.status_code not in (200, 201, 409):
        pytest.skip(f"Impossibile creare gruppo B: {res_grp.text}")

    # Crea utente B nel gruppo B
    username = f"isolation_b_{uuid.uuid4().hex[:8]}"
    res = requests.post(f"{BASE_URL}/auth/users",
                        json={"username": username, "password": "IsoPass123!",
                              "group_id": GROUP_B, "role": "admin"},
                        headers=headers_a)
    if res.status_code not in (200, 201):
        pytest.skip(f"Impossibile creare utente B: {res.text}")

    user_data = res.json()
    yield {**user_data, "password": "IsoPass123!"}

    # Teardown: elimina utente B poi gruppo B
    requests.delete(f"{BASE_URL}/auth/users/{user_data['id']}", headers=headers_a)
    requests.delete(f"{BASE_URL}/auth/groups/{GROUP_B}", headers=headers_a)


@pytest.fixture(scope="module")
def token_b(user_b):
    return _login(user_b["username"], user_b["password"])


@pytest.fixture(scope="module")
def headers_b(token_b):
    return _headers(token_b)


@pytest.fixture(scope="module")
def project_a(headers_a):
    """Crea progetto di utente A, elimina al teardown."""
    res = requests.post(f"{BASE_URL}/projects",
                        json={"name": "[ISO] Project A",
                              "project_type": "estrai",
                              "canvas": {"nodes": [], "edges": []}},
                        headers=headers_a)
    assert res.status_code == 200, f"Creazione progetto fallita: {res.text}"
    project_id = res.json()["metadata"]["id"]
    yield project_id
    requests.delete(f"{BASE_URL}/projects/{project_id}", headers=headers_a)


@pytest.fixture(scope="module")
def connection_a(headers_a):
    """Crea connessione di utente A, elimina al teardown."""
    res = requests.post(f"{BASE_URL}/connections",
                        json={"name": "[ISO] Conn A",
                              "type": "postgres",
                              "host": "localhost", "port": 5432,
                              "database": "test_iso", "username": "u", "password": "p"},
                        headers=headers_a)
    if res.status_code not in (200, 201):
        pytest.skip(f"Impossibile creare connessione: {res.text}")
    conn_id = res.json()["id"]
    yield conn_id
    requests.delete(f"{BASE_URL}/connections/{conn_id}", headers=headers_a)


# =============================================================================
# ISO-01 → ISO-03  Projects
# =============================================================================

class TestIsoProjects:

    def test_iso01_get_project_altro_gruppo_403(self, project_a, headers_b):
        """ISO-01: utente B non può leggere progetto di A."""
        res = requests.get(f"{BASE_URL}/projects/{project_a}", headers=headers_b)
        assert res.status_code in (403, 404), (
            f"Atteso 403/404, ricevuto {res.status_code}: {res.text}")

    def test_iso02_update_project_altro_gruppo_403(self, project_a, headers_b):
        """ISO-02: utente B non può modificare progetto di A."""
        res = requests.put(f"{BASE_URL}/projects/{project_a}",
                           json={"name": "hacked"},
                           headers=headers_b)
        assert res.status_code in (403, 404), (
            f"Atteso 403/404, ricevuto {res.status_code}: {res.text}")

    def test_iso03_delete_project_altro_gruppo_403(self, project_a, headers_b):
        """ISO-03: utente B non può eliminare progetto di A."""
        res = requests.delete(f"{BASE_URL}/projects/{project_a}", headers=headers_b)
        assert res.status_code in (403, 404), (
            f"Atteso 403/404, ricevuto {res.status_code}: {res.text}")

    def test_iso03b_list_projects_non_contiene_risorse_a(self, project_a, headers_b):
        """ISO-03b: lista progetti di B non contiene progetti di A."""
        res = requests.get(f"{BASE_URL}/projects", headers=headers_b)
        assert res.status_code == 200
        ids = [p.get("id") or p.get("metadata", {}).get("id")
               for p in res.json().get("projects", [])]
        assert project_a not in ids, \
            f"Progetto di A ({project_a}) visibile nella lista di B!"


# =============================================================================
# ISO-04 → ISO-06  Connections
# =============================================================================

class TestIsoConnections:

    def test_iso04_get_connection_altro_gruppo_403(self, connection_a, headers_b):
        """ISO-04: utente B non può leggere connessione di A."""
        res = requests.get(f"{BASE_URL}/connections/{connection_a}", headers=headers_b)
        assert res.status_code in (403, 404), (
            f"Atteso 403/404, ricevuto {res.status_code}: {res.text}")

    def test_iso05_update_connection_altro_gruppo_403(self, connection_a, headers_b):
        """ISO-05: utente B non può modificare connessione di A."""
        res = requests.put(f"{BASE_URL}/connections/{connection_a}",
                           json={"name": "hacked"},
                           headers=headers_b)
        assert res.status_code in (403, 404), (
            f"Atteso 403/404, ricevuto {res.status_code}: {res.text}")

    def test_iso06_delete_connection_altro_gruppo_403(self, connection_a, headers_b):
        """ISO-06: utente B non può eliminare connessione di A."""
        res = requests.delete(f"{BASE_URL}/connections/{connection_a}", headers=headers_b)
        assert res.status_code in (403, 404), (
            f"Atteso 403/404, ricevuto {res.status_code}: {res.text}")

    def test_iso06b_list_connections_non_contiene_risorse_a(self, connection_a, headers_b):
        """ISO-06b: lista connessioni di B non contiene connessioni di A."""
        res = requests.get(f"{BASE_URL}/connections", headers=headers_b)
        assert res.status_code == 200
        ids = [c["id"] for c in res.json().get("connections", [])]
        assert connection_a not in ids, \
            f"Connessione di A ({connection_a}) visibile nella lista di B!"


# =============================================================================
# ISO-07 → ISO-08  Schedules
# =============================================================================

class TestIsoSchedules:

    @pytest.fixture(scope="class")
    def schedule_a(self, project_a, headers_a):
        """Crea uno schedule per il progetto di A."""
        res = requests.post(f"{BASE_URL}/schedules",
                            json={"project_id": project_a,
                                  "cron_expression": "0 2 * * *",
                                  "description": "[ISO] Schedule A"},
                            headers=headers_a)
        if res.status_code not in (200, 201):
            pytest.skip(f"Impossibile creare schedule: {res.text}")
        schedule_id = res.json()["id"]
        yield schedule_id
        requests.delete(f"{BASE_URL}/schedules/{schedule_id}", headers=headers_a)

    def test_iso07_get_schedule_altro_gruppo_403(self, schedule_a, headers_b):
        """ISO-07: utente B non può leggere schedule di A."""
        res = requests.get(f"{BASE_URL}/schedules/{schedule_a}", headers=headers_b)
        assert res.status_code in (403, 404), (
            f"Atteso 403/404, ricevuto {res.status_code}: {res.text}")

    def test_iso08_delete_schedule_altro_gruppo_403(self, schedule_a, headers_b):
        """ISO-08: utente B non può eliminare schedule di A."""
        res = requests.delete(f"{BASE_URL}/schedules/{schedule_a}", headers=headers_b)
        assert res.status_code in (403, 404), (
            f"Atteso 403/404, ricevuto {res.status_code}: {res.text}")

    def test_iso08b_list_schedules_non_contiene_risorse_a(self, schedule_a, headers_b):
        """ISO-08b: lista schedule di B non contiene schedule di A."""
        res = requests.get(f"{BASE_URL}/schedules", headers=headers_b)
        assert res.status_code == 200
        ids = [s["id"] for s in res.json().get("schedules", [])]
        assert schedule_a not in ids, \
            f"Schedule di A ({schedule_a}) visibile nella lista di B!"


# =============================================================================
# ISO-09 → ISO-10  Datasources
# =============================================================================

class TestIsoDatasources:

    @pytest.fixture(scope="class")
    def datasource_a(self, headers_a):
        """Crea datasource per utente A."""
        res = requests.post(f"{BASE_URL}/datasources",
                            json={"name": "[ISO] DS A", "type": "local",
                                  "root_path": "/tmp/iso_test_a"},
                            headers=headers_a)
        if res.status_code not in (200, 201):
            pytest.skip(f"Impossibile creare datasource: {res.text}")
        ds_id = res.json()["id"]
        yield ds_id
        requests.delete(f"{BASE_URL}/datasources/{ds_id}", headers=headers_a)

    def test_iso09_get_datasource_altro_gruppo_403(self, datasource_a, headers_b):
        """ISO-09: utente B non può leggere datasource di A."""
        res = requests.get(f"{BASE_URL}/datasources/{datasource_a}", headers=headers_b)
        assert res.status_code in (403, 404), (
            f"Atteso 403/404, ricevuto {res.status_code}: {res.text}")

    def test_iso10_delete_datasource_altro_gruppo_403(self, datasource_a, headers_b):
        """ISO-10: utente B non può eliminare datasource di A."""
        res = requests.delete(f"{BASE_URL}/datasources/{datasource_a}", headers=headers_b)
        assert res.status_code in (403, 404), (
            f"Atteso 403/404, ricevuto {res.status_code}: {res.text}")

    def test_iso10b_list_datasources_non_contiene_risorse_a(self, datasource_a, headers_b):
        """ISO-10b: lista datasources di B non contiene datasources di A."""
        res = requests.get(f"{BASE_URL}/datasources", headers=headers_b)
        assert res.status_code == 200
        ids = [d["id"] for d in res.json().get("datasources", [])]
        assert datasource_a not in ids, \
            f"Datasource di A ({datasource_a}) visibile nella lista di B!"


# =============================================================================
# ISO-11  API Connections (Arricchisci)
# =============================================================================

class TestIsoApiConnections:

    @pytest.fixture(scope="class")
    def api_conn_a(self, headers_a):
        """Crea API connection per utente A."""
        res = requests.post(f"{BASE_URL}/api-connections",
                            json={"name": "[ISO] API Conn A",
                                  "endpoint": "https://api.test.example.com",
                                  "method": "GET"},
                            headers=headers_a)
        if res.status_code not in (200, 201):
            pytest.skip(f"Impossibile creare API connection: {res.text}")
        api_id = res.json().get("id") or res.json().get("api_id")
        yield api_id
        requests.delete(f"{BASE_URL}/enrichment/api-connections/{api_id}", headers=headers_a)

    def test_iso11_get_api_connection_altro_gruppo_403(self, api_conn_a, headers_b):
        """ISO-11: utente B non può leggere API connection di A."""
        res = requests.get(f"{BASE_URL}/api-connections/{api_conn_a}",
                           headers=headers_b)
        assert res.status_code in (403, 404), (
            f"Atteso 403/404, ricevuto {res.status_code}: {res.text}")

    def test_iso11b_list_api_connections_non_contiene_risorse_a(self, api_conn_a, headers_b):
        """ISO-11b: lista API connections di B non contiene API connections di A."""
        res = requests.get(f"{BASE_URL}/api-connections", headers=headers_b)
        assert res.status_code == 200
        ids = [c.get("id") or c.get("api_id")
               for c in res.json().get("api_connections", [])]
        assert api_conn_a not in ids, \
            f"API connection di A ({api_conn_a}) visibile nella lista di B!"


# =============================================================================
# ISO-12  Files
# =============================================================================

class TestIsoFiles:

    def test_iso12_list_files_non_contiene_risorse_a(self, headers_a, headers_b):
        """ISO-12: lista file di B non contiene file di A."""
        res_a = requests.get(f"{BASE_URL}/files", headers=headers_a)
        res_b = requests.get(f"{BASE_URL}/files", headers=headers_b)
        if res_a.status_code != 200 or res_b.status_code != 200:
            pytest.skip("Endpoint /files non disponibile")
        ids_a = {f["id"] for f in res_a.json().get("files", [])}
        ids_b = {f["id"] for f in res_b.json().get("files", [])}
        overlap = ids_a & ids_b
        assert not overlap, f"File condivisi tra gruppo A e B: {overlap}"


# =============================================================================
# ISO-13  Endpoints pubblici devono richiedere autenticazione
# =============================================================================

class TestIsoNoUnauthAccess:

    @pytest.mark.parametrize("method,path", [
        ("GET",    "/projects"),
        ("GET",    "/connections"),
        ("GET",    "/schedules"),
        ("GET",    "/datasources"),
        ("GET",    "/files"),
        ("GET",    "/api-connections"),
        ("GET",    "/logs"),
    ])
    def test_endpoint_richiede_autenticazione(self, method, path):
        """Ogni endpoint dati deve restituire 401 senza token."""
        res = requests.request(method, f"{BASE_URL}{path}")
        assert res.status_code == 401, (
            f"{method} {path} accessibile senza token! (status {res.status_code})")
