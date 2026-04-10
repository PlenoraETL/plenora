"""
tests/performance/unit/conftest.py
====================================
Fixture aggiuntive per test unit di performance (no backend, no DB).
"""

import sys
sys.path.insert(0, r"C:\Users\Marco\Desktop\int_efm\URBY-ETL\backend")

import pytest
import numpy as np
import pandas as pd
from unittest.mock import MagicMock


@pytest.fixture(scope="session")
def mock_pg_engine():
    """Engine SQLAlchemy mockato per test CARICA unit."""
    engine = MagicMock()
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    engine.connect.return_value = conn
    engine.begin.return_value = conn
    return engine


@pytest.fixture(scope="session")
def mock_connection_service():
    """ConnectionService mockato."""
    svc = MagicMock()
    svc.get_connection.return_value = MagicMock(
        id="conn_fake", type="postgres", host="localhost",
        port=5432, database="testdb", username="u", password="p"
    )
    return svc


@pytest.fixture
def make_df_with_duplicates():
    """DataFrame con valori duplicati per test dedup enrichment."""
    def _make(n_total: int, n_unique: int):
        rng = np.random.default_rng(42)
        unique_vals = [f"città_{i}" for i in range(n_unique)]
        cities = rng.choice(unique_vals, size=n_total)
        return pd.DataFrame({
            "id": np.arange(n_total),
            "città": cities,
            "valore": rng.uniform(0, 100, size=n_total),
        })
    return _make
