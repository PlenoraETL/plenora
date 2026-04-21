"""Bench formalizzato: `op_geo_clean_topology` nuovo impl vs legacy.

Il legacy ha un worst-case O(n^2) nel step "remove overlaps":

    for i in range(1, len(geometries)):
        preceding = unary_union([g for g in geometries[:i] ...])
        diff = geometries[i].difference(preceding)

cioe' ricostruisce da zero l'unione dei precedenti ad ogni passo.

Il nuovo impl usa accumulator incrementale:
    accumulated = accumulated.union(diff)

che riduce il lavoro a ~O(n) union + O(n) difference.

Baseline misurata in Batch 0:
  - 50 polys → 0.059s
  - 100 polys → 0.254s
  - 200 polys → 1.060s   (scaling 17.9x su 4x dati, O(n^2) reale)

Esecuzione:
    pytest backend/tests2/performance/test_bench_op_geo_clean_topology.py \
        -m perf_bench -s -q
"""

from __future__ import annotations

import statistics
import time
from typing import Callable, List

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import Polygon
from shapely.ops import unary_union

from app.core.manipola.transforms.spatial import op_geo_clean_topology


# ─────────────────────────────────────────────────────────────────────
# Oracle: legacy impl (verbatim)
# ─────────────────────────────────────────────────────────────────────


def _legacy_op_geo_clean_topology(
    df, geom_column, snap_tolerance=0.001,
    remove_overlaps=True, fill_gaps=True,
):
    try:
        from shapely.validation import make_valid as _make_valid
        HAS_MAKE_VALID = True
    except ImportError:
        HAS_MAKE_VALID = False

    try:
        gdf = df
        result = gdf.copy()
        fixed_count = 0

        if HAS_MAKE_VALID:
            for idx in result.index:
                geom = result.at[idx, result.geometry.name]
                if geom is not None and not geom.is_valid:
                    result.at[idx, result.geometry.name] = _make_valid(geom)
                    fixed_count += 1
        else:
            for idx in result.index:
                geom = result.at[idx, result.geometry.name]
                if geom is not None and not geom.is_valid:
                    result.at[idx, result.geometry.name] = geom.buffer(0)
                    fixed_count += 1

        if fill_gaps and snap_tolerance > 0:
            result[result.geometry.name] = (
                result.geometry
                .buffer(snap_tolerance)
                .buffer(-snap_tolerance)
            )

        if remove_overlaps and len(result) > 1:
            geom_col_name = result.geometry.name
            geometries = list(result.geometry)
            for i in range(1, len(geometries)):
                if geometries[i] is None or geometries[i].is_empty:
                    continue
                preceding = unary_union(
                    [g for g in geometries[:i] if g is not None and not g.is_empty]
                )
                diff = geometries[i].difference(preceding)
                if not diff.is_empty:
                    geometries[i] = diff
                    fixed_count += 1
            result[geom_col_name] = geometries

        result = result[~result.geometry.is_empty].copy()
        return result
    except Exception:
        return df


# ─────────────────────────────────────────────────────────────────────
# Dataset generator
# ─────────────────────────────────────────────────────────────────────


def _make_overlapping_polys(n: int, seed: int = 42, side: float = 100.0):
    """Quadrati con overlap parziali tra vicini (come make_geo_polygons_gdf
    di conftest, ma self-contained qui per isolation)."""
    rng = np.random.default_rng(seed)
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
        {"id": range(n), "geometry": polys},
        geometry="geometry", crs="EPSG:3857",
    )


# ─────────────────────────────────────────────────────────────────────
# Harness
# ─────────────────────────────────────────────────────────────────────


def _median_runtime(
    fn: Callable, args: tuple, kwargs: dict,
    warmup: int = 1, rounds: int = 3,
) -> float:
    for _ in range(warmup):
        fn(*args, **kwargs)
    times: List[float] = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn(*args, **kwargs)
        times.append(time.perf_counter() - t0)
    return statistics.median(times)


# ─────────────────────────────────────────────────────────────────────
# Assertions
# ─────────────────────────────────────────────────────────────────────

_MIN_SPEEDUP_VS_LEGACY = 3.0


@pytest.mark.perf_bench
@pytest.mark.parametrize("n", [100, 200])
def test_bench_clean_topology_speedup_vs_legacy(n):
    """Il nuovo op_geo_clean_topology deve essere >= 3x piu' veloce del
    legacy su 100/200 poligoni con overlap. Baseline legacy su 200
    polys: 1.06s (O(n^2)).
    """
    gdf = _make_overlapping_polys(n)
    kwargs = dict(
        geom_column="geometry",
        snap_tolerance=0.001,
        remove_overlaps=True,
        fill_gaps=True,
    )

    t_legacy = _median_runtime(
        _legacy_op_geo_clean_topology, (gdf,), kwargs, warmup=1, rounds=2
    )
    t_new = _median_runtime(op_geo_clean_topology, (gdf,), kwargs, warmup=1, rounds=3)

    speedup = t_legacy / t_new

    print(
        f"\n[bench clean_topology n={n:>4}] "
        f"new={t_new*1000:>8.1f}ms  "
        f"legacy={t_legacy*1000:>8.1f}ms  "
        f"speedup={speedup:>5.2f}x"
    )

    assert speedup >= _MIN_SPEEDUP_VS_LEGACY, (
        f"op_geo_clean_topology regressione vs legacy: "
        f"{speedup:.2f}x < {_MIN_SPEEDUP_VS_LEGACY}x a n={n}. "
        f"new={t_new*1000:.1f}ms, legacy={t_legacy*1000:.1f}ms"
    )
