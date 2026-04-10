"""
tests/performance/conftest.py
==============================
Fixture comuni e soglie per tutti i test di performance (PERF-01→PERF-15).
"""

import pytest
import numpy as np
import pandas as pd
import time
import uuid
import requests
from contextlib import contextmanager

# =============================================================================
# MARKER
# =============================================================================

def pytest_configure(config):
    config.addinivalue_line("markers", "perf: Test di performance con soglie esplicite")
    config.addinivalue_line("markers", "slow: Test lenti (> 30s)")


# =============================================================================
# SOGLIE (secondi)
# =============================================================================

THRESHOLDS = {
    "PERF01_preview":       5,
    "PERF02_upload":       60,
    "PERF03_join":        120,
    "PERF04_dissolve":     60,
    "PERF05_pivot":        30,
    "PERF06_enrichment": 1100,
    "PERF07_dedup":        10,
    "PERF08_preview":       5,
    "PERF09_write_100k":   30,
    "PERF10_write_500k":  120,
    "PERF11_feature_svc":  60,
    "PERF14_concurrent":   10,
    "PERF15_scheduler":    90,
}

# =============================================================================
# VOLUMI
# =============================================================================

VOLUMES = {
    "small":   1_000,
    "medium":  10_000,
    "large":   100_000,
    "xlarge":  500_000,
    "xxlarge": 1_000_000,
}

# =============================================================================
# TIMER CONTEXT MANAGER
# =============================================================================

@contextmanager
def assert_faster_than(seconds: float, label: str = ""):
    """Context manager che fallisce se il blocco supera la soglia."""
    start = time.perf_counter()
    yield
    elapsed = time.perf_counter() - start
    assert elapsed < seconds, (
        f"{label} ha impiegato {elapsed:.2f}s — soglia: {seconds}s"
    )


@pytest.fixture
def timer():
    """Fixture che restituisce il context manager assert_faster_than."""
    return assert_faster_than


# =============================================================================
# FIXTURE DATI SINTETICI — DataFrame
# =============================================================================

def _make_df(n: int, with_geometry: bool = False) -> pd.DataFrame:
    """Genera un DataFrame sintetico con n righe."""
    rng = np.random.default_rng(42)
    df = pd.DataFrame({
        "id":          np.arange(n),
        "categoria":   rng.choice(["A", "B", "C", "D"], size=n),
        "valore":      rng.uniform(0, 1000, size=n),
        "quantita":    rng.integers(1, 100, size=n),
        "data":        pd.date_range("2020-01-01", periods=n, freq="1min"),
        "regione":     rng.choice(["Nord", "Centro", "Sud", "Isole"], size=n),
        "codice":      [f"COD{i:06d}" for i in range(n)],
        "flag":        rng.choice([True, False], size=n),
        "lat":         rng.uniform(36.0, 47.0, size=n),
        "lon":         rng.uniform(6.0, 19.0, size=n),
    })
    return df


@pytest.fixture(scope="session")
def df_small():
    return _make_df(VOLUMES["small"])


@pytest.fixture(scope="session")
def df_medium():
    return _make_df(VOLUMES["medium"])


@pytest.fixture(scope="session")
def df_large():
    return _make_df(VOLUMES["large"])


@pytest.fixture(scope="session")
def df_xlarge():
    return _make_df(VOLUMES["xlarge"])


@pytest.fixture(scope="session")
def df_xxlarge():
    return _make_df(VOLUMES["xxlarge"])


@pytest.fixture(scope="session")
def df_2m_pivot():
    """DataFrame 2M righe ottimizzato per test pivot."""
    rng = np.random.default_rng(42)
    n = 2_000_000
    return pd.DataFrame({
        "id":       np.arange(n),
        "categoria": rng.choice(["A", "B", "C", "D", "E"], size=n),
        "anno":     rng.choice([2020, 2021, 2022, 2023], size=n),
        "valore":   rng.uniform(0, 1000, size=n),
    })


# =============================================================================
# FIXTURE DATI SINTETICI — GeoDataFrame
# =============================================================================

@pytest.fixture(scope="session")
def gdf_10k_points():
    """GeoDataFrame 10k punti casuali sull'Italia."""
    try:
        import geopandas as gpd
        from shapely.geometry import Point
        rng = np.random.default_rng(42)
        n = 10_000
        lats = rng.uniform(36.0, 47.0, size=n)
        lons = rng.uniform(6.0, 19.0, size=n)
        return gpd.GeoDataFrame(
            {"id": np.arange(n), "valore": rng.uniform(0, 100, size=n)},
            geometry=[Point(lon, lat) for lon, lat in zip(lons, lats)],
            crs="EPSG:4326"
        )
    except ImportError:
        pytest.skip("GeoPandas non disponibile")


@pytest.fixture(scope="session")
def gdf_50k_polygons():
    """GeoDataFrame 50k poligoni casuali (bounding box 1x1 gradi)."""
    try:
        import geopandas as gpd
        from shapely.geometry import box
        rng = np.random.default_rng(42)
        n = 50_000
        lons = rng.uniform(6.0, 18.0, size=n)
        lats = rng.uniform(36.0, 46.0, size=n)
        size = 0.01  # ~1km
        geoms = [box(lon, lat, lon + size, lat + size)
                 for lon, lat in zip(lons, lats)]
        return gpd.GeoDataFrame(
            {"id": np.arange(n),
             "categoria": rng.choice(["A", "B", "C"], size=n),
             "valore": rng.uniform(0, 100, size=n)},
            geometry=geoms,
            crs="EPSG:4326"
        )
    except ImportError:
        pytest.skip("GeoPandas non disponibile")


@pytest.fixture(scope="session")
def gdf_100k_polygons():
    """GeoDataFrame 100k poligoni casuali."""
    try:
        import geopandas as gpd
        from shapely.geometry import box
        rng = np.random.default_rng(42)
        n = 100_000
        lons = rng.uniform(6.0, 18.0, size=n)
        lats = rng.uniform(36.0, 46.0, size=n)
        size = 0.01
        geoms = [box(lon, lat, lon + size, lat + size)
                 for lon, lat in zip(lons, lats)]
        return gpd.GeoDataFrame(
            {"id": np.arange(n),
             "categoria": rng.choice(["A", "B", "C", "D"], size=n),
             "valore": rng.uniform(0, 100, size=n)},
            geometry=geoms,
            crs="EPSG:4326"
        )
    except ImportError:
        pytest.skip("GeoPandas non disponibile")


# =============================================================================
# FIXTURE — Connessione backend
# =============================================================================

BASE_URL = "http://localhost:8000/api"
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
def make_project(auth_headers):
    created = []

    def _make(name=None, project_type="estrai"):
        name = name or f"[PERF] {uuid.uuid4().hex[:8]}"
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


# =============================================================================
# HELPER QUALITÀ OUTPUT
# =============================================================================

def assert_df_quality(df: pd.DataFrame,
                      min_rows: int = 1,
                      max_null_pct: float = 0.01,
                      label: str = "DataFrame"):
    """Asserzioni standard sulla qualità di un DataFrame."""
    assert df is not None, f"{label}: DataFrame è None"
    assert not df.empty, f"{label}: DataFrame è vuoto"
    assert len(df) >= min_rows, f"{label}: {len(df)} righe < attese {min_rows}"
    null_pct = df.isnull().sum().sum() / (len(df) * len(df.columns))
    assert null_pct <= max_null_pct, \
        f"{label}: {null_pct:.1%} valori null > soglia {max_null_pct:.1%}"


def assert_gdf_quality(gdf, min_rows: int = 1, label: str = "GeoDataFrame"):
    """Asserzioni standard sulla qualità di un GeoDataFrame."""
    try:
        import geopandas as gpd
    except ImportError:
        return
    assert_df_quality(gdf, min_rows=min_rows, label=label)
    assert gdf.geometry is not None, f"{label}: colonna geometry mancante"
    assert gdf.geometry.notna().all(), f"{label}: geometrie null trovate"
    invalid = (~gdf.geometry.is_valid).sum()
    assert invalid == 0, f"{label}: {invalid} geometrie non valide"
    assert gdf.crs is not None, f"{label}: CRS mancante"


@pytest.fixture
def quality():
    """Fixture che espone le funzioni di qualità."""
    return {
        "df": assert_df_quality,
        "gdf": assert_gdf_quality,
    }


# =============================================================================
# FIXTURE — Connessione PostgreSQL reale
# =============================================================================

@pytest.fixture(scope="session")
def pg_connection_id(auth_headers):
    """Crea una connessione PostgreSQL di test e la rimuove al teardown."""
    import requests as req
    res = req.post(f"{BASE_URL}/connections",
                   json={"name": "[PERF] pg test",
                         "type": "postgres",
                         "host": "localhost", "port": 5432,
                         "database": "testdb",
                         "username": "postgres", "password": "postgres"},
                   headers=auth_headers)
    if res.status_code not in (200, 201):
        pytest.skip(f"PostgreSQL non disponibile: {res.text}")
    conn_id = res.json()["id"]
    yield conn_id
    req.delete(f"{BASE_URL}/connections/{conn_id}", headers=auth_headers)


# =============================================================================
# FIXTURE — DataFrame con duplicati per test dedup
# =============================================================================

@pytest.fixture
def make_df_with_duplicates():
    """Factory: DataFrame con n_total righe e n_unique valori unici."""
    def _make(n_total: int, n_unique: int):
        rng = np.random.default_rng(42)
        unique_vals = [f"città_{i}" for i in range(n_unique)]
        cities = rng.choice(unique_vals, size=n_total)
        return pd.DataFrame({
            "id":     np.arange(n_total),
            "città":  cities,
            "valore": rng.uniform(0, 100, size=n_total),
        })
    return _make


# =============================================================================
# FIXTURE — Connessione mock (per unit test)
# =============================================================================

@pytest.fixture
def mock_pg_engine():
    """Engine SQLAlchemy mockato per test CARICA unit."""
    from unittest.mock import MagicMock
    engine = MagicMock()
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    engine.connect.return_value = conn
    engine.begin.return_value = conn
    return engine
