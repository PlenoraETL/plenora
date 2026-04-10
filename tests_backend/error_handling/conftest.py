"""
tests/error_handling/conftest.py
=================================
Fixture comuni per tutti i test ERR-01→ERR-30.
"""

import pytest
import requests
import uuid

BASE_URL  = "http://localhost:8000/api"
TEST_USER = "admin"
TEST_PASS = "admin123"


@pytest.fixture(scope="session")
def auth_token() -> str:
    res = requests.post(f"{BASE_URL}/auth/login",
                        json={"username": TEST_USER, "password": TEST_PASS})
    assert res.status_code == 200, f"Login fallito: {res.text}"
    return res.json()["access_token"]


@pytest.fixture(scope="session")
def auth_headers(auth_token) -> dict:
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture
def nonexistent_id() -> str:
    return f"nonexistent_{uuid.uuid4().hex}"


@pytest.fixture
def make_project(auth_headers):
    created = []

    def _make(name=None, project_type="estrai"):
        name = name or f"[ERR-TEST] {uuid.uuid4().hex[:8]}"
        res = requests.post(f"{BASE_URL}/projects",
                            json={"name": name, "project_type": project_type,
                                  "canvas": {"nodes": [], "edges": []}},
                            headers=auth_headers)
        assert res.status_code == 200, f"Creazione progetto fallita: {res.text}"
        project_id = res.json()["metadata"]["id"]
        created.append(project_id)
        return project_id

    yield _make

    for pid in created:
        requests.delete(f"{BASE_URL}/projects/{pid}", headers=auth_headers)


@pytest.fixture
def make_connection(auth_headers):
    created = []

    def _make(name=None, db_type="postgresql", host="localhost", port=5432,
              database="testdb", username="u", password="p"):
        name = name or f"[ERR-TEST] {uuid.uuid4().hex[:8]}"
        res = requests.post(f"{BASE_URL}/connections",
                            json={"name": name, "type": db_type, "host": host,
                                  "port": port, "database": database,
                                  "username": username, "password": password},
                            headers=auth_headers)
        if res.status_code not in (200, 201):
            pytest.skip(f"Impossibile creare connessione: {res.text}")
        conn_id = res.json()["id"]
        created.append(conn_id)
        return conn_id

    yield _make

    for cid in created:
        requests.delete(f"{BASE_URL}/connections/{cid}", headers=auth_headers)


@pytest.fixture
def make_schedule(auth_headers, make_project):
    created = []

    def _make(project_id=None, cron="0 2 * * *"):
        if project_id is None:
            project_id = make_project()
        res = requests.post(f"{BASE_URL}/schedules",
                            json={"project_id": project_id,
                                  "cron_expression": cron,
                                  "description": "[ERR-TEST]"},
                            headers=auth_headers)
        if res.status_code not in (200, 201):
            pytest.skip(f"Impossibile creare schedule: {res.text}")
        schedule_id = res.json()["id"]
        created.append(schedule_id)
        return schedule_id

    yield _make

    for sid in created:
        requests.delete(f"{BASE_URL}/schedules/{sid}", headers=auth_headers)
