"""
Bench formalizzato: `op_geo_voronoi` nuovo impl vs legacy.

Il legacy associa punto->poligono con `for idx, row in gdf.iterrows():
"""

from __future__ import annotations

import statistics
import time
from typing import Callable, List

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import Point

from app.core.manipola.transforms.spatial import op_geo_voronoi


# Oracle: legacy impl (verbatim)


def _legacy_op_geo_voronoi(df, geom_column):
    try:
        from shapely.geometry import MultiPoint
        from shapely.ops import voronoi_diagram as _voronoi_diagram

        gdf = df
        points = gdf.geometry.tolist()
        multipoint = MultiPoint(points)
        voronoi_geom = _voronoi_diagram(multipoint)
        voronoi_polygons = list(voronoi_geom.geoms)

        result_rows = []
        for idx, row in gdf.iterrows():
            pt = row.geometry
            matched_poly = None
            for poly in voronoi_polygons:
                if poly.contains(pt) or poly.touches(pt):
                    matched_poly = poly
                    break
            if matched_poly is None:
                distances = [poly.distance(pt) for poly in voronoi_polygons]
                matched_poly = voronoi_polygons[distances.index(min(distances))]
            result_rows.append(matched_poly)

        result = gdf.copy()
        result["geometry"] = result_rows
        return result
    except Exception:
        return df


# Harness


def _make_points(n: int, seed: int = 42) -> gpd.GeoDataFrame:
    rng = np.random.default_rng(seed)
    xs = rng.uniform(0, 10_000, size=n)
    ys = rng.uniform(0, 10_000, size=n)
    pts = [Point(x, y) for x, y in zip(xs, ys)]
    return gpd.GeoDataFrame(
        {"id": range(n), "geometry": pts},
        geometry="geometry",
        crs="EPSG:3857",
    )


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


# Assertions

_MIN_SPEEDUP_VS_LEGACY = 3.0


@pytest.mark.perf_bench
@pytest.mark.parametrize("n", [500, 1000])
def test_bench_geo_voronoi_speedup_vs_legacy(n):
    """Il nuovo op_geo_voronoi deve essere >= 3x piu' veloce del legacy
    su 500/1000 punti. Baseline legacy: 2.15s su 1000 punti."""
    gdf = _make_points(n)
    kwargs = dict(geom_column="geometry")

    # NOTE: legacy in genere ha rounds=2 per ridurre tempo totale bench
    t_legacy = _median_runtime(
        _legacy_op_geo_voronoi, (gdf,), kwargs, warmup=1, rounds=2
    )
    t_new = _median_runtime(op_geo_voronoi, (gdf,), kwargs, warmup=1, rounds=3)

    speedup = t_legacy / t_new

    print(
        f"\n[bench voronoi n={n:>5}] "
        f"new={t_new*1000:>8.1f}ms  "
        f"legacy={t_legacy*1000:>8.1f}ms  "
        f"speedup={speedup:>5.2f}x"
    )

    assert speedup >= _MIN_SPEEDUP_VS_LEGACY, (
        f"op_geo_voronoi regressione vs legacy: "
        f"{speedup:.2f}x < {_MIN_SPEEDUP_VS_LEGACY}x a n={n}. "
        f"new={t_new*1000:.1f}ms, legacy={t_legacy*1000:.1f}ms"
    )
