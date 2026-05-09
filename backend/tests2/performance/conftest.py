"""
Fixture e helper comuni per i test perf di stream-perf.

Obiettivi:
"""

from __future__ import annotations

import string
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Tuple

import numpy as np
import pandas as pd
import pytest


# Marker registration


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "perf_baseline: baseline measurement dello stato attuale (pre-optimization)",
    )
    config.addinivalue_line(
        "markers",
        "perf_bench: benchmark post-optimization vs baseline (atteso speedup >=3x)",
    )
    config.addinivalue_line(
        "markers",
        "perf_correctness: invariant preservation test (output identico pre/post)",
    )


# Timing helper


@dataclass
class TimingResult:
    name: str
    n_rows: int
    seconds: float
    rows_per_sec: float


def time_call(name: str, n_rows: int, fn: Callable, *args, **kwargs) -> TimingResult:
    """Misura il tempo di una singola chiamata. Per baseline e' sufficiente:
    un singolo run puo' essere rumoroso, ma lo usiamo per ordini di grandezza."""
    t0 = time.perf_counter()
    fn(*args, **kwargs)
    dt = time.perf_counter() - t0
    rps = n_rows / dt if dt > 0 else float("inf")
    return TimingResult(name=name, n_rows=n_rows, seconds=dt, rows_per_sec=rps)


def print_timing(tr: TimingResult) -> None:
    """Stampa in formato riga-tabella-markdown (integrabile in STREAM_REPORT.md)."""
    print(
        f"| {tr.name:<45} | {tr.n_rows:>10,} | {tr.seconds:>8.4f}s | "
        f"{tr.rows_per_sec:>12,.0f} rows/s |"
    )


# Synthetic dataset generators


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def make_pandas_df(n: int, seed: int = 42, include_geo: bool = False) -> pd.DataFrame:
    """DataFrame con mix di tipi tipici ETL: int, float, str, date, bool.
    Nessuna dipendenza su shapely se include_geo=False."""
    rng = _rng(seed)
    data = {
        "id": np.arange(n, dtype=np.int64),
        "category": rng.choice(["A", "B", "C", "D"], size=n),
        "amount": rng.uniform(0, 10_000, size=n).round(2),
        "qty": rng.integers(0, 1000, size=n),
        "active": rng.choice([True, False], size=n),
        "created_at": pd.to_datetime("2024-01-01")
        + pd.to_timedelta(rng.integers(0, 365, size=n), unit="D"),
        "label": np.array(
            [
                "".join(rng.choice(list(string.ascii_letters), size=8))
                for _ in range(n)
            ]
        ),
    }
    return pd.DataFrame(data)


def make_diff_inputs(
    n: int, seed: int = 42, changed_ratio: float = 0.1
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Genera (old, new) dove:
    - 80% righe invariate
    - changed_ratio righe modificate sulla colonna `amount`
    - 5% righe aggiunte (solo in new)
    - 5% righe rimosse (solo in old)
    Gli id sono stabili per permettere il merge by id."""
    rng = _rng(seed)
    old = make_pandas_df(n, seed=seed)

    # NEW = copia di OLD...
    new = old.copy()

    # ...con changed_ratio righe modificate (amount)
    n_changed = int(n * changed_ratio)
    changed_idx = rng.choice(n, size=n_changed, replace=False)
    new.loc[changed_idx, "amount"] = new.loc[changed_idx, "amount"] + 1000.0

    # ...con 5% righe rimosse in new
    n_removed = max(1, int(n * 0.05))
    removed_idx = rng.choice(n, size=n_removed, replace=False)
    new = new.drop(index=removed_idx).reset_index(drop=True)

    # ...con 5% righe aggiunte in new (nuovi id)
    n_added = max(1, int(n * 0.05))
    extra = make_pandas_df(n_added, seed=seed + 1)
    extra["id"] = np.arange(n, n + n_added, dtype=np.int64)
    new = pd.concat([new, extra], ignore_index=True)

    return old, new


def make_geo_points_gdf(n: int, seed: int = 42):
    """GeoDataFrame di punti random dentro un bbox EPSG:3857.
    Import lazy di geopandas/shapely per evitare side effects in file perf
    che non servono geo."""
    import geopandas as gpd
    from shapely.geometry import Point

    rng = _rng(seed)
    xs = rng.uniform(0, 100_000, size=n)
    ys = rng.uniform(0, 100_000, size=n)
    geoms = [Point(x, y) for x, y in zip(xs, ys)]
    return gpd.GeoDataFrame(
        {"id": np.arange(n), "geometry": geoms},
        geometry="geometry",
        crs="EPSG:3857",
    )


def make_geo_polygons_gdf(n: int, seed: int = 42, side: float = 100.0):
    """GeoDataFrame di quadrati con overlap parziali tra vicini.
    Usato per testare op_geo_clean_topology (remove_overlaps path)."""
    import geopandas as gpd
    from shapely.geometry import Polygon

    rng = _rng(seed)
    polys = []
    for i in range(n):
        cx = (i % 100) * (side * 0.8) + rng.uniform(-5, 5)
        cy = (i // 100) * (side * 0.8) + rng.uniform(-5, 5)
        polys.append(
            Polygon(
                [
                    (cx, cy),
                    (cx + side, cy),
                    (cx + side, cy + side),
                    (cx, cy + side),
                ]
            )
        )
    return gpd.GeoDataFrame(
        {"id": np.arange(n), "geometry": polys},
        geometry="geometry",
        crs="EPSG:3857",
    )


# Pytest fixtures


@pytest.fixture
def small_df():
    return make_pandas_df(1_000)


@pytest.fixture
def medium_df():
    return make_pandas_df(10_000)


@pytest.fixture
def large_df():
    return make_pandas_df(100_000)


@pytest.fixture(scope="session")
def perf_report_dir() -> Path:
    """Dir dove i test perf possono scrivere artefatti (timing CSV, ecc.).
    Sotto il worktree, gitignorata in pratica (non committata di default)."""
    d = Path(__file__).resolve().parent / "_artifacts"
    d.mkdir(exist_ok=True)
    return d
