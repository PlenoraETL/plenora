"""Correctness invariant: `op_geo_voronoi` post-refactor == impl legacy.

Il refactor di Batch 3A sostituisce il `for idx, row in gdf.iterrows()`
+ loop lineare su tutti i voronoi_polygons con uno STRtree query.
L'output deve restare bit-per-bit identico: ogni input point deve
essere associato allo stesso poligono di Voronoi che il legacy
gli avrebbe assegnato.

Coperture richieste dal vincolo del checkpoint:
  - **correttezza topologica**: ogni poligono di Voronoi ritornato per
    il punto i-esimo deve essere esattamente il poligono che il legacy
    avrebbe selezionato (first-match per contains/touches, fallback
    nearest-distance)
  - **ordine-preservante**: se un punto e' al confine tra piu' celle
    (touches su entrambi), il legacy picca quello con index minore
    nella lista voronoi_polygons — anche noi dobbiamo
  - **fallback nearest**: casi degeneri (punto fuori da tutti, che
    non dovrebbe succedere con voronoi_diagram ma capita se il
    diagramma e' ritagliato)
  - **colonne preservate**: gdf input ha attributi, result deve
    preservare attributi + index
  - **input non mutato**

Esecuzione:
    pytest backend/tests2/performance/test_correctness_op_geo_voronoi.py \
        -m perf_correctness -s -q
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import Point

from app.core.manipola.transforms.spatial import op_geo_voronoi


# ─────────────────────────────────────────────────────────────────────
# Oracle: legacy impl (verbatim da spatial.py:1252 @ 7f024be)
# ─────────────────────────────────────────────────────────────────────


def _legacy_op_geo_voronoi(df, geom_column):
    """Copia verbatim dell'impl pre-Batch-3A: iterrows su punti +
    loop lineare su tutti i voronoi_polygons."""
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


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _assert_geometries_equal(new_gdf, legacy_gdf, ctx):
    """Per ogni riga, la geometria del new_gdf e' topologicamente uguale
    al legacy. Usa `.equals_exact` con tolerance 1e-9 per catturare
    divergenze oltre il rumore FP; se qualche coppia fallisce, prova
    `.equals` (topological) come seconda chance prima di dichiarare
    divergenza reale."""
    assert len(new_gdf) == len(legacy_gdf), (
        f"[{ctx}] lunghezza diff: new={len(new_gdf)} vs legacy={len(legacy_gdf)}"
    )
    diffs_exact = []
    diffs_topological = []
    for i, (gn, gl) in enumerate(zip(new_gdf.geometry, legacy_gdf.geometry)):
        if gn is None and gl is None:
            continue
        if gn is None or gl is None:
            diffs_exact.append((i, "None vs not-None"))
            continue
        # Check exact (coordinate-level) equality
        if not gn.equals_exact(gl, 1e-9):
            # Fallback: topological equality
            if gn.equals(gl):
                diffs_topological.append(i)
            else:
                diffs_exact.append((i, "geom differs"))
                if len(diffs_exact) >= 3:
                    break
    if diffs_exact:
        raise AssertionError(
            f"[{ctx}] divergenza geometrica. Primi diff (idx, ragione): "
            f"{diffs_exact}"
        )
    if diffs_topological:
        # Report as info, not failure — le .equals_exact failures che
        # sono .equals true sono differenze FP accettabili
        print(
            f"\n[{ctx}] INFO: {len(diffs_topological)} geometrie "
            f"topologicamente uguali ma non bit-exact (FP noise, ok)"
        )


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


def _gdf_small_grid():
    """10 punti su griglia 2x5. Voronoi cells molto prevedibili."""
    pts = [Point(x, y) for x in (0, 10) for y in (0, 10, 20, 30, 40)]
    return gpd.GeoDataFrame(
        {"id": range(len(pts)), "geometry": pts},
        geometry="geometry",
        crs="EPSG:3857",
    )


def _gdf_random_cluster():
    """50 punti random in un bbox piccolo."""
    rng = np.random.default_rng(42)
    xs = rng.uniform(0, 100, size=50)
    ys = rng.uniform(0, 100, size=50)
    pts = [Point(x, y) for x, y in zip(xs, ys)]
    return gpd.GeoDataFrame(
        {"id": range(50), "geometry": pts, "label": [f"p{i}" for i in range(50)]},
        geometry="geometry",
        crs="EPSG:3857",
    )


def _gdf_collinear():
    """Punti collineari: caso degenere per voronoi (celle rettangolari
    estese). Stress test per matching contains vs touches."""
    pts = [Point(i * 10.0, 0.0) for i in range(8)]
    return gpd.GeoDataFrame(
        {"id": range(8), "geometry": pts},
        geometry="geometry",
        crs="EPSG:3857",
    )


def _gdf_two_points():
    """Caso minimo: 2 punti → 2 celle. Test sanity."""
    return gpd.GeoDataFrame(
        {"id": [0, 1], "geometry": [Point(0, 0), Point(10, 10)]},
        geometry="geometry",
        crs="EPSG:3857",
    )


def _gdf_duplicate_coords():
    """Punti con coordinate identiche: voronoi_diagram restituisce
    meno celle dei punti. Legacy fallback nearest si attiva."""
    pts = [Point(0, 0), Point(0, 0), Point(10, 10), Point(20, 20)]
    return gpd.GeoDataFrame(
        {"id": [0, 1, 2, 3], "geometry": pts},
        geometry="geometry",
        crs="EPSG:3857",
    )


def _gdf_non_monotonic_index():
    """Indice non-monotono: stress test che result preservi indice."""
    pts = [Point(x, y) for x, y in [(0, 0), (10, 0), (0, 10), (10, 10)]]
    gdf = gpd.GeoDataFrame(
        {"id": range(4), "geometry": pts},
        geometry="geometry",
        crs="EPSG:3857",
    )
    gdf.index = [100, 3, 7, 42]
    return gdf


_CASES = [
    ("small grid 2x5", _gdf_small_grid),
    ("random cluster 50", _gdf_random_cluster),
    ("collinear 8pts", _gdf_collinear),
    ("two points only", _gdf_two_points),
    ("duplicate coords", _gdf_duplicate_coords),
    ("non-monotonic index", _gdf_non_monotonic_index),
]


# ─────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.perf_correctness
@pytest.mark.parametrize(
    "label,gdf_factory",
    _CASES,
    ids=[c[0] for c in _CASES],
)
def test_op_geo_voronoi_invariant(label, gdf_factory):
    """Per ogni caso edge, ogni poligono Voronoi del nuovo impl ==
    quello del legacy per la stessa riga."""
    gdf = gdf_factory()
    snapshot_len = len(gdf)
    snapshot_index = list(gdf.index)

    new_out = op_geo_voronoi(gdf, geom_column="geometry")
    legacy_out = _legacy_op_geo_voronoi(gdf, geom_column="geometry")

    _assert_geometries_equal(new_out, legacy_out, label)

    # Preserva colonne non-geom
    for col in gdf.columns:
        if col == "geometry":
            continue
        assert col in new_out.columns, f"[{label}] colonna '{col}' persa"
        assert list(new_out[col]) == list(gdf[col]), (
            f"[{label}] colonna '{col}' alterata"
        )

    # Preserva indice
    assert list(new_out.index) == snapshot_index, (
        f"[{label}] indice alterato"
    )

    # Input non mutato (lunghezza)
    assert len(gdf) == snapshot_len, f"[{label}] input mutato"


@pytest.mark.perf_correctness
def test_op_geo_voronoi_large_scale_invariant():
    """200 punti random: invariance di scala vs legacy."""
    rng = np.random.default_rng(42)
    n = 200
    xs = rng.uniform(0, 1000, size=n)
    ys = rng.uniform(0, 1000, size=n)
    pts = [Point(x, y) for x, y in zip(xs, ys)]
    gdf = gpd.GeoDataFrame(
        {"id": range(n), "geometry": pts},
        geometry="geometry",
        crs="EPSG:3857",
    )

    new_out = op_geo_voronoi(gdf, geom_column="geometry")
    legacy_out = _legacy_op_geo_voronoi(gdf, geom_column="geometry")

    _assert_geometries_equal(new_out, legacy_out, "large_scale_200")


@pytest.mark.perf_correctness
def test_op_geo_voronoi_result_is_geodataframe():
    """Output deve essere un GeoDataFrame con geometria attiva."""
    gdf = _gdf_small_grid()
    result = op_geo_voronoi(gdf, geom_column="geometry")
    assert isinstance(result, gpd.GeoDataFrame)
    assert result.geometry.name == "geometry"
    # Tutti i poligoni non devono essere None (voronoi copre ogni punto)
    assert result.geometry.notna().all(), "Voronoi ha prodotto None"
