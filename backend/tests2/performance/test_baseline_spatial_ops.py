"""Baseline timing: operazioni geospaziali NON ancora ottimizzate.

Al contrario di test_baseline_pandas_ops.py, qui il "current" E' il
baseline vero (nessuna vettorizzazione landed). Il refactor di Batch 3
(spatial sindex + unary_union chunked) dovra' battere questi numeri
con speedup >=3x.

Hotspot noti, pre-Batch-3:
  - op_geo_voronoi (spatial.py:1252): iterrows su punti + contains
    seriale su voronoi_polygons -> O(n^2)
  - op_geo_clean_topology (spatial.py:1519):
      * python-loop make_valid cella per cella
      * **unary_union accumulator chiamato dentro for-loop** -> O(n^2)
        reale, il peggiore dei 3 hotspot

Esecuzione:
    pytest backend/tests2/performance/test_baseline_spatial_ops.py \
        -m perf_baseline -s -q
"""

from __future__ import annotations

import pytest

from app.core.manipola.transforms.spatial import (
    op_geo_clean_topology,
    op_geo_voronoi,
)

from .conftest import make_geo_points_gdf, make_geo_polygons_gdf, print_timing, time_call


@pytest.mark.perf_baseline
def test_baseline_op_geo_voronoi_scaling():
    """op_geo_voronoi con dataset 250/500/1000 punti per vedere scaling O(n^2).

    La parte veloce e' la costruzione del voronoi_diagram (shapely in C).
    La parte lenta e' l'associazione punto->poligono: per ogni riga fa un
    loop Python su TUTTI i poligoni voronoi finche' trova `contains` o
    `touches`. Se nessun match, fallback: distanze a tutti i poligoni.
    """
    print("\n## op_geo_voronoi baseline (scaling O(n^2))")
    print(
        "| impl                                       |  n_points |   seconds |   rows/s    |"
    )
    print(
        "|--------------------------------------------|----------:|----------:|------------:|"
    )

    for n in (250, 500, 1000):
        gdf = make_geo_points_gdf(n)
        tr = time_call(
            "current iterrows op_geo_voronoi",
            n,
            op_geo_voronoi,
            gdf,
            geom_column="geometry",
        )
        print_timing(tr)


@pytest.mark.perf_baseline
def test_baseline_op_geo_clean_topology_scaling():
    """op_geo_clean_topology con 50/100/200 poligoni.

    Il remove_overlaps ha il pattern:
        for i in range(1, len(geometries)):
            preceding = unary_union(geometries[:i])
            diff = geometries[i].difference(preceding)

    cioe' per ogni poligono ricostruisce da zero l'unione dei precedenti
    -> n chiamate unary_union ciascuna su O(n) input -> O(n^2) reale.

    Il make_valid iniziale ha un python-loop su `result.index`: cella per
    cella. Vectorizable con result.geometry = result.geometry.make_valid().
    """
    print("\n## op_geo_clean_topology baseline (scaling O(n^2) su unary_union)")
    print(
        "| impl                                       |  n_polys  |   seconds |   rows/s    |"
    )
    print(
        "|--------------------------------------------|----------:|----------:|------------:|"
    )

    for n in (50, 100, 200):
        gdf = make_geo_polygons_gdf(n)
        tr = time_call(
            "current O(n^2) op_geo_clean_topology",
            n,
            op_geo_clean_topology,
            gdf,
            geom_column="geometry",
            snap_tolerance=0.001,
            remove_overlaps=True,
            fill_gaps=True,
        )
        print_timing(tr)
