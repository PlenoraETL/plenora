"""
tests/regression/conftest.py
=============================
Fixture comuni per Regression + Production Readiness suite.
"""

import pytest
import json
import numpy as np
import pandas as pd
import uuid
import requests
import time
from pathlib import Path
from unittest.mock import MagicMock

# =============================================================================
# MARKER
# =============================================================================

def pytest_configure(config):
    config.addinivalue_line("markers", "regression: Test di regressione")
    config.addinivalue_line("markers", "production: Test di production readiness")
    config.addinivalue_line("markers", "slow: Test lenti (> 30s)")
    config.addinivalue_line("markers", "e2e: Test end-to-end")

# =============================================================================
# BASELINE HELPERS
# =============================================================================

BASELINES_DIR = Path(__file__).parent / "baselines"
BASELINES_DIR.mkdir(exist_ok=True)


def save_baseline(name: str, data: dict):
    path = BASELINES_DIR / f"{name}.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def load_baseline(name: str) -> dict:
    path = BASELINES_DIR / f"{name}.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def assert_baseline(name: str, data: dict, tolerance: float = 0.01):
    """Confronta con baseline salvato. Se non esiste, lo crea."""
    baseline = load_baseline(name)
    if baseline is None:
        save_baseline(name, data)
        pytest.skip(f"Baseline '{name}' creato — rilancia per confrontare")
    for key in baseline:
        if key in data:
            bval = baseline[key]
            dval = data[key]
            if isinstance(bval, (int, float)) and isinstance(dval, (int, float)):
                assert abs(dval - bval) / max(abs(bval), 1) <= tolerance, \
                    f"Baseline mismatch '{key}': {dval} vs {bval}"


@pytest.fixture
def baseline():
    return {"save": save_baseline, "load": load_baseline, "assert": assert_baseline}


# =============================================================================
# FIXTURE BACKEND
# =============================================================================

BASE_URL = "http://localhost:8000/api"
TEST_USER = "admin"
TEST_PASS = "admin123"


@pytest.fixture(scope="session")
def auth_token():
    res = requests.post(f"{BASE_URL}/auth/login",
                        json={"username": TEST_USER, "password": TEST_PASS})
    assert res.status_code == 200, f"Login fallito: {res.text}"
    return res.json()["access_token"]


@pytest.fixture(scope="session")
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture
def make_project(auth_headers):
    created = []
    def _make(name=None, project_type="estrai"):
        name = name or f"[REG] {uuid.uuid4().hex[:8]}"
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
def make_connection(auth_headers):
    created = []
    def _make(name=None, db_type="postgres", host="localhost", port=5432,
              database="testdb", username="postgres", password="postgres"):
        name = name or f"[REG] {uuid.uuid4().hex[:8]}"
        res = requests.post(f"{BASE_URL}/connections",
                            json={"name": name, "type": db_type, "host": host,
                                  "port": port, "database": database,
                                  "username": username, "password": password},
                            headers=auth_headers)
        if res.status_code not in (200, 201):
            pytest.skip(f"Connessione non creabile: {res.text}")
        conn_id = res.json()["id"]
        created.append(conn_id)
        return conn_id
    yield _make
    for cid in created:
        requests.delete(f"{BASE_URL}/connections/{cid}", headers=auth_headers)


@pytest.fixture
def nonexistent_id():
    return f"nonexistent_{uuid.uuid4().hex}"


# =============================================================================
# FIXTURE DATI SINTETICI
# =============================================================================

@pytest.fixture(scope="session")
def df_pg_100():
    """Simula 100 righe da PostgreSQL (REG-01)."""
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        "id":        np.arange(100),
        "nome":      [f"record_{i}" for i in range(100)],
        "valore":    rng.uniform(0, 1000, 100),
        "categoria": rng.choice(["A", "B", "C"], 100),
        "data":      pd.date_range("2020-01-01", periods=100, freq="1D"),
    })


@pytest.fixture(scope="session")
def gdf_gpkg():
    """Simula layer GeoPackage (REG-02)."""
    try:
        import geopandas as gpd
        from shapely.geometry import Point
        rng = np.random.default_rng(42)
        n = 500
        return gpd.GeoDataFrame(
            {"id": np.arange(n), "nome": [f"feat_{i}" for i in range(n)],
             "valore": rng.uniform(0, 100, n)},
            geometry=[Point(rng.uniform(6,19), rng.uniform(36,47))
                      for _ in range(n)],
            crs="EPSG:4326"
        )
    except ImportError:
        pytest.skip("GeoPandas non disponibile")


@pytest.fixture(scope="session")
def gdf_shapefile():
    """Simula Shapefile .zip (REG-03)."""
    try:
        import geopandas as gpd
        from shapely.geometry import Point
        rng = np.random.default_rng(42)
        n = 200
        return gpd.GeoDataFrame(
            {"id": np.arange(n), "area_kmq": rng.uniform(1, 100, n)},
            geometry=[Point(rng.uniform(6,19), rng.uniform(36,47))
                      for _ in range(n)],
            crs="EPSG:32632"
        )
    except ImportError:
        pytest.skip("GeoPandas non disponibile")


@pytest.fixture(scope="session")
def df_csv_latlon():
    """Simula CSV con lat/lon (REG-04)."""
    rng = np.random.default_rng(42)
    n = 300
    return pd.DataFrame({
        "id":   np.arange(n),
        "nome": [f"punto_{i}" for i in range(n)],
        "lat":  rng.uniform(36, 47, n),
        "lon":  rng.uniform(6, 19, n),
        "val":  rng.uniform(0, 100, n),
    })


@pytest.fixture(scope="session")
def df_filter_large():
    """Simula dataset 166k righe per filter test (REG-11)."""
    rng = np.random.default_rng(42)
    n = 166_075
    return pd.DataFrame({
        "id":        np.arange(n),
        "categoria": rng.choice(["A", "B", "C", "D", "E"], n),
        "valore":    rng.uniform(0, 1000, n),
        "regione":   rng.choice(["Nord", "Centro", "Sud"], n),
        "flag":      rng.choice([True, False], n),
    })


@pytest.fixture(scope="session")
def df_paesi():
    """Simula dataset paesi per enrichment (REG-17→REG-27)."""
    return pd.DataFrame({
        "id":     [1, 2, 3, 4, 5],
        "paese":  ["Italy", "France", "Germany", "Spain", "Poland"],
        "codice": ["ITA", "FRA", "DEU", "ESP", "POL"],
        "pop":    [60_000_000, 67_000_000, 83_000_000, 47_000_000, 38_000_000],
    })


@pytest.fixture(scope="session")
def df_paesi_10():
    """10 righe con 3 paesi unici (REG-20)."""
    rng = np.random.default_rng(42)
    paesi = rng.choice(["Italy", "France", "Germany"], 10)
    return pd.DataFrame({
        "id":    np.arange(10),
        "paese": paesi,
        "val":   rng.uniform(0, 100, 10),
    })


# =============================================================================
# MOCK API HELPERS
# =============================================================================

def mock_rest_countries_response(country: str) -> dict:
    """Risposta mock REST Countries per paese."""
    data = {
        "Italy":   {"capital": ["Rome"], "population": 60_000_000, "area": 301_340},
        "France":  {"capital": ["Paris"], "population": 67_000_000, "area": 551_695},
        "Germany": {"capital": ["Berlin"], "population": 83_000_000, "area": 357_114},
        "Spain":   {"capital": ["Madrid"], "population": 47_000_000, "area": 505_990},
        "Poland":  {"capital": ["Warsaw"], "population": 38_000_000, "area": 312_696},
    }
    return data.get(country, {"capital": ["Unknown"], "population": 0, "area": 0})


def mock_open_meteo_response(lat: float, lon: float) -> dict:
    """Risposta mock Open-Meteo."""
    return {
        "current": {
            "temperature_2m": 20.5,
            "wind_speed_10m": 5.2,
        }
    }


@pytest.fixture
def mock_rest_countries():
    return mock_rest_countries_response


@pytest.fixture
def mock_open_meteo():
    return mock_open_meteo_response
