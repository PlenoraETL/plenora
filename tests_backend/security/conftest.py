"""
tests/security/conftest.py
===========================
Fixture condivise per tutta la suite security.

Le fixture di infrastruttura (pg_conn, pg_table, make_project, poll, client)
sono ereditate dal conftest.py root.
"""

import pytest
import requests
import uuid

BASE_URL  = "http://localhost:8000/api"
TEST_USER = "admin"
TEST_PASS = "admin123"


# ─── Fixture base auth ────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def auth_token() -> str:
    """JWT token valido per l'utente admin — riusato per tutta la sessione."""
    res = requests.post(f"{BASE_URL}/auth/login",
                        json={"username": TEST_USER, "password": TEST_PASS})
    assert res.status_code == 200, f"Login fallito: {res.text}"
    return res.json()["access_token"]


@pytest.fixture(scope="session")
def auth_headers(auth_token) -> dict:
    """Header Authorization Bearer per l'utente admin."""
    return {"Authorization": f"Bearer {auth_token}"}


# ─── Fixture secondo utente (per group isolation) ─────────────────────────────

@pytest.fixture(scope="session")
def second_user(auth_headers) -> dict:
    """
    Crea un secondo utente nello stesso gruppo per i test di group isolation.
    Lo elimina al teardown.
    """
    username = f"test_sec_{uuid.uuid4().hex[:8]}"
    res = requests.post(f"{BASE_URL}/auth/users",
                        json={"username": username, "password": "SecPass123!",
                              "group_id": "default"},
                        headers=auth_headers)
    if res.status_code != 201:
        pytest.skip(f"Impossibile creare secondo utente: {res.text}")

    user_data = res.json()
    yield user_data

    # Teardown
    requests.delete(f"{BASE_URL}/auth/users/{user_data['id']}",
                    headers=auth_headers)


@pytest.fixture
def second_user_token(second_user) -> str:
    """Token JWT per il secondo utente."""
    res = requests.post(f"{BASE_URL}/auth/login",
                        json={"username": second_user["username"],
                              "password": "SecPass123!"})
    assert res.status_code == 200
    return res.json()["access_token"]


@pytest.fixture
def second_user_headers(second_user_token) -> dict:
    return {"Authorization": f"Bearer {second_user_token}"}


# ─── Fixture token temporanei ─────────────────────────────────────────────────

@pytest.fixture
def fresh_token() -> str:
    """Token fresco ad ogni test (scope function)."""
    res = requests.post(f"{BASE_URL}/auth/login",
                        json={"username": TEST_USER, "password": TEST_PASS})
    assert res.status_code == 200
    return res.json()["access_token"]


@pytest.fixture
def fresh_headers(fresh_token) -> dict:
    return {"Authorization": f"Bearer {fresh_token}"}


# ─── Fixture utente temporaneo ────────────────────────────────────────────────

@pytest.fixture
def temp_user(auth_headers) -> dict:
    """
    Crea un utente temporaneo per il singolo test e lo elimina al teardown.
    """
    username = f"tmp_{uuid.uuid4().hex[:8]}"
    res = requests.post(f"{BASE_URL}/auth/users",
                        json={"username": username, "password": "TmpPass123!",
                              "group_id": "default"},
                        headers=auth_headers)
    if res.status_code != 201:
        pytest.skip(f"Impossibile creare utente temporaneo: {res.text}")

    user_data = res.json()
    yield {**user_data, "password": "TmpPass123!"}

    requests.delete(f"{BASE_URL}/auth/users/{user_data['id']}",
                    headers=auth_headers)


@pytest.fixture
def temp_user_token(temp_user) -> str:
    res = requests.post(f"{BASE_URL}/auth/login",
                        json={"username": temp_user["username"],
                              "password": temp_user["password"]})
    assert res.status_code == 200
    return res.json()["access_token"]
