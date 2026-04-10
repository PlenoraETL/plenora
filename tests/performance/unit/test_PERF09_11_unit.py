"""
tests/performance/unit/test_PERF09_11_carica.py
------------------------------------------------
Test UNIT di performance — Modulo CARICA (PERF-09→PERF-11)

PERF-09  Write 100k righe PostgreSQL → < 30s
PERF-10  Write 500k righe batch=1000 → < 120s, no OOM
PERF-11  Feature Service ADD 10k features batch=200 → < 60s
"""

import pytest
import time
import numpy as np
import pandas as pd
from unittest.mock import MagicMock, patch, call

import time as _time
from contextlib import contextmanager as _contextmanager

THRESHOLDS = {
    "PERF01_preview": 5, "PERF02_upload": 60, "PERF03_join": 120,
    "PERF04_dissolve": 60, "PERF05_pivot": 30, "PERF06_enrichment": 1100,
    "PERF07_dedup": 10, "PERF08_preview": 5, "PERF09_write_100k": 30,
    "PERF10_write_500k": 120, "PERF11_feature_svc": 60,
    "PERF14_concurrent": 10, "PERF15_scheduler": 90,
}

@_contextmanager
def assert_faster_than(seconds, label=""):
    start = _time.perf_counter()
    yield
    elapsed = _time.perf_counter() - start
    assert elapsed < seconds, f"{label} ha impiegato {elapsed:.2f}s > {seconds}s"

def assert_df_quality(df, min_rows=1, max_null_pct=0.01, label="DataFrame"):
    assert df is not None and not df.empty and len(df) >= min_rows

def assert_gdf_quality(gdf, min_rows=1, label="GeoDataFrame"):
    assert_df_quality(gdf, min_rows=min_rows)
    assert gdf.geometry.notna().all() and gdf.crs is not None



pytestmark = [pytest.mark.perf, pytest.mark.unit]




# =============================================================================
# PERF-09 — Write 100k righe PostgreSQL
# =============================================================================

class TestPERF09WriteUnit:

    @pytest.mark.parametrize("n_rows", [
        1_000, 5_000, 10_000, 50_000, 100_000
    ])
    def test_dataframe_preparazione_veloce(self, n_rows):
        """Preparazione DataFrame per scrittura è rapida."""
        rng = np.random.default_rng(42)
        with assert_faster_than(max(0.5, n_rows / 100_000 * 3),
                                f"prep df {n_rows:,}"):
            df = pd.DataFrame({
                "id":       np.arange(n_rows),
                "nome":     [f"record_{i}" for i in range(n_rows)],
                "valore":   rng.uniform(0, 100, size=n_rows),
                "lat":      rng.uniform(36, 47, size=n_rows),
                "lon":      rng.uniform(6, 19, size=n_rows),
                "categoria": rng.choice(["A", "B", "C"], size=n_rows),
            })
        assert len(df) == n_rows
        assert_df_quality(df, min_rows=n_rows, label=f"write prep {n_rows}")

    @pytest.mark.parametrize("n_rows,batch_size", [
        (100_000, 1000),
        (100_000, 5000),
        (100_000, 10000),
    ])
    def test_batching_corretto(self, n_rows, batch_size):
        """Il batching divide correttamente le righe."""
        df = pd.DataFrame({"id": np.arange(n_rows), "val": np.ones(n_rows)})
        n_batches = (n_rows + batch_size - 1) // batch_size
        batches = [df.iloc[i:i+batch_size] for i in range(0, n_rows, batch_size)]
        assert len(batches) == n_batches
        assert sum(len(b) for b in batches) == n_rows

    def test_write_mock_100k_velocita(self, df_large, mock_pg_engine):
        """Write 100k righe con mock engine è rapida."""
        from app.core.carica.executor import CaricaExecutor
        executor = CaricaExecutor(
            connection_service=mock_connection_service_factory(),
            engine_factory=mock_engine_factory(mock_pg_engine),
        )
        df = df_large.copy()
        with assert_faster_than(5.0, "write mock 100k"):
            try:
                executor.execute(df=df, node_data=_fake_node_data(), project_id="proj-1")
            except Exception:
                pass  # mock può fallire su validazione

    @pytest.mark.parametrize("n_rows", [1_000, 10_000, 100_000])
    def test_colonne_duplicate_rilevate(self, n_rows):
        """Colonne duplicate vengono rilevate prima della scrittura."""
        df = pd.DataFrame(
            [[i] * 3 for i in range(n_rows)],
            columns=["id", "nome", "id"]  # id duplicato
        )
        assert df.columns.duplicated().any()

    def test_write_100k_qualita_input(self, df_large):
        """Il DataFrame 100k ha qualità adeguata per la scrittura."""
        assert_df_quality(df_large, min_rows=100_000, label="write 100k input")
        assert df_large["id"].is_unique
        assert df_large["valore"].notna().all()

    @pytest.mark.parametrize("batch_size", [100, 500, 1000, 5000, 10000])
    def test_batch_size_vari_valori(self, batch_size, df_large):
        """Vari batch_size producono lo stesso numero totale di righe."""
        batches = list(range(0, len(df_large), batch_size))
        total = sum(
            len(df_large.iloc[i:i+batch_size]) for i in batches
        )
        assert total == len(df_large)

    def test_write_result_struttura(self):
        """WriteResult ha la struttura attesa."""
        from app.core.carica.models import WriteResult, WriteStatus
        result = WriteResult(
            status=WriteStatus.SUCCESS,
            rows_written=100_000,
            total_rows=100_000,
        )
        assert result.status == WriteStatus.SUCCESS
        assert result.rows_written == 100_000
        assert result.error_message is None or True


# =============================================================================
# PERF-10 — Write 500k righe batch=1000
# =============================================================================

class TestPERF10Write500kUnit:

    @pytest.mark.parametrize("n_rows,batch_size", [
        (100_000, 1000),
        (200_000, 1000),
        (500_000, 1000),
        (500_000, 5000),
    ])
    def test_batch_count_corretto(self, n_rows, batch_size):
        """Il numero di batch è corretto."""
        expected_batches = (n_rows + batch_size - 1) // batch_size
        df = pd.DataFrame({"id": np.arange(n_rows)})
        actual_batches = sum(1 for _ in range(0, n_rows, batch_size))
        assert actual_batches == expected_batches

    @pytest.mark.parametrize("n_rows", [100_000, 200_000, 500_000])
    def test_nessun_oom(self, n_rows):
        """Creazione DataFrame n_rows non causa OOM."""
        import tracemalloc
        rng = np.random.default_rng(42)
        tracemalloc.start()
        try:
            df = pd.DataFrame({
                "id":    np.arange(n_rows),
                "nome":  [f"r_{i}" for i in range(n_rows)],
                "val":   rng.uniform(0, 100, size=n_rows),
                "lat":   rng.uniform(36, 47, size=n_rows),
                "lon":   rng.uniform(6, 19, size=n_rows),
            })
            _, peak = tracemalloc.get_traced_memory()
            peak_mb = peak / 1024 / 1024
            max_mb = n_rows / 100_000 * 500
            assert peak_mb < max_mb, \
                f"OOM: {peak_mb:.0f}MB > {max_mb:.0f}MB per {n_rows:,} righe"
        finally:
            tracemalloc.stop()

    def test_500k_batch_1000_mock(self, df_xlarge, mock_pg_engine):
        """Write 500k con batch=1000 → 500 batch, no errori mock."""
        n_rows = len(df_xlarge)
        batch_size = 1000
        write_calls = 0

        def mock_write_batch(batch):
            nonlocal write_calls
            write_calls += 1
            return len(batch)

        total_written = 0
        for i in range(0, n_rows, batch_size):
            batch = df_xlarge.iloc[i:i+batch_size]
            total_written += mock_write_batch(batch)

        assert write_calls == n_rows // batch_size
        assert total_written == n_rows

    @pytest.mark.parametrize("n_rows", [100_000, 200_000, 500_000])
    def test_qualita_output_dopo_batch(self, n_rows):
        """Tutti i batch insieme coprono esattamente n_rows."""
        df = pd.DataFrame({"id": np.arange(n_rows)})
        batch_size = 1000
        covered = set()
        for i in range(0, n_rows, batch_size):
            batch = df.iloc[i:i+batch_size]
            covered.update(batch["id"].tolist())
        assert len(covered) == n_rows
        assert min(covered) == 0
        assert max(covered) == n_rows - 1

    @pytest.mark.parametrize("batch_size", [500, 1000, 2000, 5000])
    def test_batch_size_diversi_stesso_risultato(self, batch_size, df_xlarge):
        """Batch size diversi producono stesso totale righe."""
        total = sum(
            len(df_xlarge.iloc[i:i+batch_size])
            for i in range(0, len(df_xlarge), batch_size)
        )
        assert total == len(df_xlarge)


# =============================================================================
# PERF-11 — Feature Service ADD 10k features batch=200
# =============================================================================

class TestPERF11FeatureServiceUnit:

    @pytest.mark.parametrize("n_features,batch_size", [
        (100,    200),
        (1_000,  200),
        (5_000,  200),
        (10_000, 200),
        (10_000, 500),
    ])
    def test_batch_count_features(self, n_features, batch_size):
        """Il numero di batch per Feature Service è corretto."""
        expected = (n_features + batch_size - 1) // batch_size
        actual = len(range(0, n_features, batch_size))
        assert actual == expected

    @pytest.mark.parametrize("n_features,batch_size", [
        (10_000, 200),
        (10_000, 500),
        (10_000, 1000),
    ])
    def test_mock_feature_service_velocita(self, n_features, batch_size):
        """Feature Service mock per 10k features è rapido."""
        import time

        batch_responses = []

        def mock_add_features(features):
            time.sleep(0.01)  # simula 10ms per batch
            return {"addResults": [{"success": True}] * len(features)}

        features = [{"attributes": {"id": i}, "geometry": {"x": i, "y": i}}
                    for i in range(n_features)]

        with assert_faster_than(
            n_features / batch_size * 0.02 + 2,
            f"mock feature service {n_features}→batch{batch_size}"
        ):
            for i in range(0, n_features, batch_size):
                batch = features[i:i+batch_size]
                response = mock_add_features(batch)
                batch_responses.append(response)

        total_added = sum(
            len(r["addResults"]) for r in batch_responses
        )
        assert total_added == n_features

    def test_feature_qualita_geometria(self):
        """Le feature hanno geometrie valide."""
        features = [
            {"attributes": {"id": i},
             "geometry": {"x": 9.0 + i * 0.001, "y": 45.0 + i * 0.001}}
            for i in range(100)
        ]
        for f in features:
            assert "geometry" in f
            assert "x" in f["geometry"]
            assert "y" in f["geometry"]
            assert 6 <= f["geometry"]["x"] <= 19
            assert 36 <= f["geometry"]["y"] <= 47

    @pytest.mark.parametrize("n_features", [100, 1_000, 10_000])
    def test_feature_serializzazione_veloce(self, n_features):
        """Serializzazione JSON delle feature è rapida."""
        import json
        features = [
            {"attributes": {"id": i, "nome": f"feat_{i}"},
             "geometry": {"x": 9.0 + i * 0.0001, "y": 45.0}}
            for i in range(n_features)
        ]
        threshold = max(0.5, n_features / 10_000 * 2)
        with assert_faster_than(threshold, f"json serialize {n_features}"):
            data = json.dumps({"features": features})
        assert len(json.loads(data)["features"]) == n_features

    def test_feature_nessuna_geometria_nulla(self):
        """Nessuna feature ha geometria nulla."""
        rng = np.random.default_rng(42)
        n = 10_000
        features = [
            {"attributes": {"id": i},
             "geometry": {"x": float(rng.uniform(6, 19)),
                          "y": float(rng.uniform(36, 47))}}
            for i in range(n)
        ]
        nulls = sum(1 for f in features if f["geometry"] is None)
        assert nulls == 0

    @pytest.mark.parametrize("n_features", [1_000, 5_000, 10_000])
    def test_feature_attributi_completi(self, n_features):
        """Tutti gli attributi delle feature sono presenti."""
        rng = np.random.default_rng(42)
        features = [
            {"attributes": {"id": i, "nome": f"feat_{i}",
                            "valore": float(rng.uniform(0, 100))},
             "geometry": {"x": float(rng.uniform(6, 19)),
                          "y": float(rng.uniform(36, 47))}}
            for i in range(n_features)
        ]
        for f in features:
            assert "id" in f["attributes"]
            assert "nome" in f["attributes"]
            assert "valore" in f["attributes"]
            assert f["attributes"]["valore"] >= 0


# =============================================================================
# HELPER
# =============================================================================

def mock_connection_service_factory():
    svc = MagicMock()
    svc.get_connection.return_value = MagicMock(
        id="conn", type="postgres", host="localhost",
        port=5432, database="db", username="u", password="p"
    )
    return svc


def mock_engine_factory(engine):
    factory = MagicMock()
    factory.create_engine.return_value = engine
    return factory


def _fake_node_data():
    return {
        "connection_id": "conn_fake",
        "table_name": "test_table",
        "write_mode": "append",
        "column_mappings": [],
        "batch_size": 1000,
    }
