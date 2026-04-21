"""Correctness invariant: `op_geo_clean_topology` post-refactor vs legacy.

Il refactor di Batch 3B sostituisce l'O(n^2) unary_union-accumulator
(`preceding = unary_union(geometries[:i])` dentro un for-loop) con
un accumulator incrementale (`accumulated = accumulated.union(diff)`
al passo i).

Rischio principale: i risultati di `unary_union([a, b, c])` e di
`a.union(b).union(c)` possono differire a livello di coordinate per
floating-point (GEOS interno e' deterministico per input fissi, ma
l'ordine e la strategia di union cambiano il cammino numerico).

Vincolo del checkpoint utente:
  "quality of data > raw speed"
  "se per unary_union emerge un tradeoff forte tra performance e
   correttezza topologica, fermati a checkpoint"

Policy di questo test:
  - Confronto primario con `equals_exact(tol=1e-9)`.
  - Se fallisce, fallback a `equals()` topologica (che usa la
    tolerance interna GEOS): se passa = "FP noise accettabile".
  - Se fallisce anche `equals()`, confronto a tolerance di area
    relativa: `symmetric_difference.area / total.area < 1e-6`.
  - Se anche quello fallisce → AssertionError + report diff.

Qualsiasi divergenza oltre la tolerance viene riportata al
checkpoint utente prima del commit.

Esecuzione:
    pytest backend/tests2/performance/test_correctness_op_geo_clean_topology.py \
        -m perf_correctness -s -q
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from app.core.manipola.transforms.spatial import op_geo_clean_topology


# ─────────────────────────────────────────────────────────────────────
# Oracle: legacy impl (verbatim da spatial.py:1519 @ 7f024be)
# ─────────────────────────────────────────────────────────────────────


def _legacy_op_geo_clean_topology(
    df, geom_column, snap_tolerance=0.001,
    remove_overlaps=True, fill_gaps=True,
):
    """Copia verbatim dell'impl pre-Batch-3B: O(n^2) unary_union
    dentro il for-loop di remove_overlaps."""
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
# Comparison helpers
# ─────────────────────────────────────────────────────────────────────


def _geom_equivalent(gn, gl, area_tol_rel: float = 1e-6) -> tuple[bool, str]:
    """True se le due geometrie sono equivalenti entro tolleranze
    accettabili. Ordinate per severita':
      1. equals_exact(1e-9): bit-identiche
      2. equals(): topologicamente uguali
      3. area(symmetric_difference)/area(total) < tol_rel: equivalenti
         entro rumore FP
    Ritorna (ok, descrizione_tipo)."""
    if gn is None and gl is None:
        return True, "both None"
    if gn is None or gl is None:
        return False, "None vs not-None"
    if gn.is_empty and gl.is_empty:
        return True, "both empty"
    if gn.is_empty or gl.is_empty:
        return False, "empty vs not-empty"
    if gn.equals_exact(gl, 1e-9):
        return True, "exact"
    if gn.equals(gl):
        return True, "topological"
    # Area-based tolerance
    try:
        total_area = max(gn.area, gl.area, 1e-30)
        sym_diff_area = gn.symmetric_difference(gl).area
        if sym_diff_area / total_area < area_tol_rel:
            return True, f"area-rel={sym_diff_area/total_area:.2e}"
    except Exception as e:
        return False, f"comparison-error: {e}"
    return False, "not-equivalent"


def _assert_gdf_equivalent(new_gdf, legacy_gdf, ctx):
    """Confronta due GeoDataFrame riga-per-riga. Deve avere stesso
    numero di righe; ogni geometria deve essere equivalente entro
    tolerance. Stampa riepilogo per tipi di match."""
    assert len(new_gdf) == len(legacy_gdf), (
        f"[{ctx}] lunghezza diff: new={len(new_gdf)} vs legacy={len(legacy_gdf)}"
    )
    match_types: dict[str, int] = {}
    diffs: list = []
    for i, (gn, gl) in enumerate(zip(new_gdf.geometry, legacy_gdf.geometry)):
        ok, kind = _geom_equivalent(gn, gl)
        if not ok:
            diffs.append((i, kind))
            if len(diffs) >= 3:
                break
        else:
            match_types[kind] = match_types.get(kind, 0) + 1
    if diffs:
        raise AssertionError(
            f"[{ctx}] divergenza geometrica. Primi diff (idx, ragione): "
            f"{diffs}. Match types finora: {match_types}"
        )
    # Report dei tipi di match (indicazione di quanto si allontana
    # dal bit-exact)
    if any(k != "exact" and k != "both empty" for k in match_types):
        print(f"\n[{ctx}] match types: {match_types}")


# ─────────────────────────────────────────────────────────────────────
# Fixtures: dataset di poligoni con vari pattern topologici
# ─────────────────────────────────────────────────────────────────────


def _square(cx, cy, side):
    return Polygon(
        [
            (cx, cy),
            (cx + side, cy),
            (cx + side, cy + side),
            (cx, cy + side),
        ]
    )


def _gdf_clean_no_overlap():
    """Poligoni gia' non sovrapposti. Il remove_overlaps non
    dovrebbe alterare niente."""
    polys = [_square(i * 20, 0, 10) for i in range(5)]
    return gpd.GeoDataFrame(
        {"id": range(5), "geometry": polys},
        geometry="geometry", crs="EPSG:3857",
    )


def _gdf_with_overlaps():
    """Poligoni con sovrapposizioni: ciascuno overlappa il successivo."""
    polys = [_square(i * 8, 0, 10) for i in range(6)]
    return gpd.GeoDataFrame(
        {"id": range(6), "geometry": polys, "tipo": ["A"] * 6},
        geometry="geometry", crs="EPSG:3857",
    )


def _gdf_with_gaps():
    """Poligoni con micro-gap (per testare fill_gaps + remove_overlaps)."""
    polys = [
        _square(0, 0, 10),
        _square(10.0005, 0, 10),  # micro-gap 0.0005
        _square(20.001, 0, 10),   # micro-gap 0.001
    ]
    return gpd.GeoDataFrame(
        {"id": range(3), "geometry": polys},
        geometry="geometry", crs="EPSG:3857",
    )


def _gdf_full_overlap_absorbed():
    """Poligono 2 completamente dentro poligono 1 — dovrebbe
    sparire dopo remove_overlaps (difference = empty)."""
    polys = [
        _square(0, 0, 100),       # big
        _square(10, 10, 20),      # inside big → diff = empty → skip
        _square(200, 0, 50),      # far, separate
    ]
    return gpd.GeoDataFrame(
        {"id": range(3), "geometry": polys},
        geometry="geometry", crs="EPSG:3857",
    )


def _gdf_with_empty_none():
    """Input con empty e None mescolati."""
    polys = [
        _square(0, 0, 10),
        None,
        Polygon(),  # empty
        _square(8, 0, 10),  # overlaps with [0]
        _square(50, 0, 10),
    ]
    return gpd.GeoDataFrame(
        {"id": range(5), "geometry": polys},
        geometry="geometry", crs="EPSG:3857",
    )


def _gdf_random_overlapping(n: int = 30, seed: int = 42):
    """Poligoni random con overlap parziali: stress test realistico."""
    rng = np.random.default_rng(seed)
    polys = []
    for _ in range(n):
        cx = rng.uniform(0, 200)
        cy = rng.uniform(0, 200)
        side = rng.uniform(15, 30)
        polys.append(_square(cx, cy, side))
    return gpd.GeoDataFrame(
        {"id": range(n), "geometry": polys},
        geometry="geometry", crs="EPSG:3857",
    )


def _gdf_single_poly():
    """Caso minimo: 1 poligono."""
    return gpd.GeoDataFrame(
        {"id": [0], "geometry": [_square(0, 0, 10)]},
        geometry="geometry", crs="EPSG:3857",
    )


def _gdf_invalid_geom():
    """Poligono non valido (bowtie): make_valid deve aggiustarlo."""
    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10), (0, 0)])
    return gpd.GeoDataFrame(
        {"id": [0, 1], "geometry": [bowtie, _square(50, 0, 10)]},
        geometry="geometry", crs="EPSG:3857",
    )


_CASES = [
    ("clean no-overlap", _gdf_clean_no_overlap, dict(snap_tolerance=0.001, remove_overlaps=True, fill_gaps=True)),
    ("with overlaps", _gdf_with_overlaps, dict(snap_tolerance=0.001, remove_overlaps=True, fill_gaps=True)),
    ("with overlaps no-fill", _gdf_with_overlaps, dict(snap_tolerance=0.001, remove_overlaps=True, fill_gaps=False)),
    ("with overlaps no-remove", _gdf_with_overlaps, dict(snap_tolerance=0.001, remove_overlaps=False, fill_gaps=True)),
    ("with micro-gaps", _gdf_with_gaps, dict(snap_tolerance=0.001, remove_overlaps=True, fill_gaps=True)),
    ("full overlap absorbed", _gdf_full_overlap_absorbed, dict(snap_tolerance=0.001, remove_overlaps=True, fill_gaps=True)),
    ("empty+None mixed", _gdf_with_empty_none, dict(snap_tolerance=0.001, remove_overlaps=True, fill_gaps=True)),
    ("random overlapping 30", _gdf_random_overlapping, dict(snap_tolerance=0.001, remove_overlaps=True, fill_gaps=True)),
    ("single poly", _gdf_single_poly, dict(snap_tolerance=0.001, remove_overlaps=True, fill_gaps=True)),
    ("invalid geometry bowtie", _gdf_invalid_geom, dict(snap_tolerance=0.001, remove_overlaps=True, fill_gaps=True)),
]


# ─────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.perf_correctness
@pytest.mark.parametrize(
    "label,gdf_factory,kwargs",
    _CASES,
    ids=[c[0] for c in _CASES],
)
def test_op_geo_clean_topology_invariant(label, gdf_factory, kwargs):
    """Per ogni caso, la topologia corretta deve essere equivalente
    fra nuovo e legacy (exact → topological → area-rel 1e-6)."""
    gdf = gdf_factory()

    new_out = op_geo_clean_topology(gdf, geom_column="geometry", **kwargs)
    legacy_out = _legacy_op_geo_clean_topology(
        gdf, geom_column="geometry", **kwargs
    )

    _assert_gdf_equivalent(new_out, legacy_out, label)


@pytest.mark.perf_correctness
def test_op_geo_clean_topology_large_scale():
    """100 poligoni random overlapping: invariance su scala moderata."""
    gdf = _gdf_random_overlapping(n=100, seed=7)
    new_out = op_geo_clean_topology(
        gdf, geom_column="geometry",
        snap_tolerance=0.001, remove_overlaps=True, fill_gaps=True,
    )
    legacy_out = _legacy_op_geo_clean_topology(
        gdf, geom_column="geometry",
        snap_tolerance=0.001, remove_overlaps=True, fill_gaps=True,
    )
    _assert_gdf_equivalent(new_out, legacy_out, "random_100")


@pytest.mark.perf_correctness
def test_op_geo_clean_topology_union_coverage_equivalent():
    """Invariante topologica di alto livello: l'unione dei poligoni
    in output deve avere la stessa area (entro tolerance) fra legacy e
    nuovo — garanzia che il 'totale della copertura' non cambi."""
    gdf = _gdf_random_overlapping(n=50, seed=11)
    kwargs = dict(
        snap_tolerance=0.001, remove_overlaps=True, fill_gaps=True
    )

    new_out = op_geo_clean_topology(gdf, geom_column="geometry", **kwargs)
    legacy_out = _legacy_op_geo_clean_topology(
        gdf, geom_column="geometry", **kwargs
    )

    new_union = unary_union(list(new_out.geometry))
    legacy_union = unary_union(list(legacy_out.geometry))

    # Area totale coperta deve essere uguale entro 1e-6 relativo
    if legacy_union.area > 0:
        rel_diff = abs(new_union.area - legacy_union.area) / legacy_union.area
        assert rel_diff < 1e-6, (
            f"Area coperta diverge: new={new_union.area:.6f} "
            f"legacy={legacy_union.area:.6f} rel_diff={rel_diff:.2e}"
        )


@pytest.mark.perf_correctness
def test_op_geo_clean_topology_no_overlap_preserves_shapes():
    """Poligoni gia' disgiunti: dopo clean, le geometrie dovrebbero
    essere pressoche' invariate (solo buffer+/- che introduce FP noise).
    Semantica legacy preservata = nuovo impl preserva lo stesso."""
    gdf = _gdf_clean_no_overlap()
    new_out = op_geo_clean_topology(
        gdf, geom_column="geometry",
        snap_tolerance=0.001, remove_overlaps=True, fill_gaps=True,
    )
    legacy_out = _legacy_op_geo_clean_topology(
        gdf, geom_column="geometry",
        snap_tolerance=0.001, remove_overlaps=True, fill_gaps=True,
    )
    _assert_gdf_equivalent(new_out, legacy_out, "clean_no_overlap")
    # Area totale = 5 * 10^2 = 500 circa
    assert abs(sum(g.area for g in new_out.geometry) - 500.0) < 0.1


@pytest.mark.perf_correctness
def test_op_geo_clean_topology_remove_overlaps_false_is_noop():
    """remove_overlaps=False + fill_gaps=False → risultato molto simile
    all'input (solo make_valid)."""
    gdf = _gdf_clean_no_overlap()
    new_out = op_geo_clean_topology(
        gdf, geom_column="geometry",
        snap_tolerance=0.0, remove_overlaps=False, fill_gaps=False,
    )
    legacy_out = _legacy_op_geo_clean_topology(
        gdf, geom_column="geometry",
        snap_tolerance=0.0, remove_overlaps=False, fill_gaps=False,
    )
    _assert_gdf_equivalent(new_out, legacy_out, "noop_path")
