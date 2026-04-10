"""
tests/logs/conftest.py
======================
Fixture comuni per la LOG suite.
"""

import pytest
import sqlite3
import uuid
import json
import requests
from pathlib import Path
from unittest.mock import MagicMock

BASE_URL = "http://localhost:8000/api"
TEST_USER = "admin"
TEST_PASS = "admin123"


def pytest_configure(config):
    config.addinivalue_line("markers", "logs: Test suite LOG")
    config.addinivalue_line("markers", "slow: Test lenti")


@pytest.fixture(scope="session")
def auth_token():
    res = requests.post(f"{BASE_URL}/auth/login",
                        json={"username": TEST_USER, "password": TEST_PASS})
    assert res.status_code == 200
    return res.json()["access_token"]


@pytest.fixture(scope="session")
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture
def make_project(auth_headers):
    created = []
    def _make(name=None, project_type="estrai"):
        name = name or f"[LOG] {uuid.uuid4().hex[:8]}"
        res = requests.post(f"{BASE_URL}/projects",
                            json={"name": name, "project_type": project_type,
                                  "canvas": {"nodes": [], "edges": []}},
                            headers=auth_headers)
        assert res.status_code == 200
        pid = res.json()["metadata"]["id"]
        created.append(pid)
        return pid
    yield _make
    for pid in created:
        requests.delete(f"{BASE_URL}/projects/{pid}", headers=auth_headers)


@pytest.fixture
def nonexistent_id():
    return f"nonexistent_{uuid.uuid4().hex}"


@pytest.fixture(scope="session")
def log_db_path():
    """Path del log.db — lo leggiamo direttamente per test unit."""
    import sys
    sys.path.insert(0, r"C:\Users\Marco\Desktop\int_efm\URBY-ETL\backend")
    from app.config import settings
    return settings.LOGS_DIR / settings.LOG_DB_FILE


@pytest.fixture
def db_conn(log_db_path):
    """Connessione diretta a log.db per verifiche."""
    if not log_db_path.exists():
        pytest.skip("log.db non esiste ancora")
    conn = sqlite3.connect(str(log_db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()
