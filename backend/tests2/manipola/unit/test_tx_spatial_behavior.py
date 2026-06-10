"""
Behavioral unit tests per app/core/manipola/transforms/spatial.py

Copre happy-path e rami significativi delle operazioni spatial pubbliche:
- helper env (_env_int) e cap (_voronoi_max_points, _clean_topology_*)
- op_geo_dissolve (aggfunc numerico vs first, by_column)
- op_geo_simplify (validazione tolerance, happy path)
- op_geo_convex_hull / op_geo_envelope / op_geo_boundary
- op_geo_explode (multi -> single)
- op_geo_make_valid (gia' valido vs invalido)
- op_geo_within (filtro spaziale 2-input + <2 input)
- op_geo_point_on_surface
- op_geo_voronoi (happy + cap)
- op_geo_to_wkt (wkt / latlon / both)
- op_geo_clean_topology (overlap removal + cap geoms/vertici)
- op_geo_bounds_extractor (prefix) / op_geo_vertex_count
- op_geo_reproject (source_crs override, fail-loud senza CRS)
- op_geo_distance / op_geo_nearest rami input vuoto
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import (
    LineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)

from app.core.manipola.transforms.spatial import (
    _clean_topology_max_geoms,
    _clean_topology_max_vertices,
    _env_int,
    _voronoi_max_points,
    op_geo_bounds_extractor,
    op_geo_boundary,
    op_geo_clean_topology,
    op_geo_convex_hull,
    op_geo_dissolve,
    op_geo_distance,
    op_geo_envelope,
    op_geo_explode,
    op_geo_make_valid,
    op_geo_nearest,
    op_geo_point_on_surface,
    op_geo_reproject,
    op_geo_simplify,
    op_geo_to_wkt,
    op_geo_vertex_count,
    op_geo_voronoi,
    op_geo_within,
)


pytestmark = [pytest.mark.unit, pytest.mark.manipola]


# Fixtures


@pytest.fixture
def gdf_two_polys():
    """Due poligoni con categoria, WGS84, intorno a Bologna."""
    lon, lat, d = 11.34, 44.49, 0.01
    p1 = Polygon([(lon - d, lat - d), (lon + d, lat - d),
                  (lon + d, lat + d), (lon - d, lat + d)])
    p2 = Polygon([(lon + 1, lat - d), (lon + 1 + 2 * d, lat - d),
                  (lon + 1 + 2 * d, lat + d), (lon + 1, lat + d)])
    return gpd.GeoDataFrame(
        {"cat": ["A", "A"], "val": [10.0, 30.0], "label": ["x", "y"]},
        geometry=[p1, p2],
        crs="EPSG:4326",
    )


@pytest.fixture
def gdf_points():
    return gpd.GeoDataFrame(
        {"id": [1, 2, 3, 4]},
        geometry=[Point(0, 0), Point(10, 0), Point(0, 10), Point(10, 10)],
        crs="EPSG:32632",
    )


# _env_int helper


def test_env_int_default_when_unset(monkeypatch):
    monkeypatch.delenv("PLENORA_TEST_CAP_X", raising=False)
    assert _env_int("PLENORA_TEST_CAP_X", 42) == 42


def test_env_int_reads_valid_positive(monkeypatch):
    monkeypatch.setenv("PLENORA_TEST_CAP_X", "7")
    assert _env_int("PLENORA_TEST_CAP_X", 42) == 7


def test_env_int_falls_back_on_non_positive(monkeypatch):
    monkeypatch.setenv("PLENORA_TEST_CAP_X", "0")
    assert _env_int("PLENORA_TEST_CAP_X", 42) == 42
    monkeypatch.setenv("PLENORA_TEST_CAP_X", "-5")
    assert _env_int("PLENORA_TEST_CAP_X", 42) == 42


def test_env_int_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("PLENORA_TEST_CAP_X", "not-an-int")
    assert _env_int("PLENORA_TEST_CAP_X", 99) == 99


def test_cap_helpers_defaults(monkeypatch):
    monkeypatch.delenv("PLENORA_GEO_VORONOI_MAX_POINTS", raising=False)
    monkeypatch.delenv("PLENORA_GEO_CLEAN_TOPOLOGY_MAX_GEOMS", raising=False)
    monkeypatch.delenv("PLENORA_GEO_CLEAN_TOPOLOGY_MAX_VERTICES", raising=False)
    assert _voronoi_max_points() == 100_000
    assert _clean_topology_max_geoms() == 50_000
    assert _clean_topology_max_vertices() == 5_000_000


# DISSOLVE


def test_dissolve_first_aggfunc_groups_by_column(gdf_two_polys):
    result = op_geo_dissolve(gdf_two_polys, "geometry", by_column="cat",
                             aggfunc="first")
    # Una sola categoria "A" -> una riga, con i due poligoni uniti
    assert len(result) == 1
    assert result["cat"].iloc[0] == "A"
    assert isinstance(result.geometry.iloc[0], (Polygon, MultiPolygon))


def test_dissolve_numeric_aggfunc_keeps_strings_as_first(gdf_two_polys):
    """aggfunc='sum' deve applicarsi solo alle numeriche; le stringhe
    restano 'first' invece di sollevare su concat di stringhe."""
    result = op_geo_dissolve(gdf_two_polys, "geometry", by_column="cat",
                             aggfunc="sum")
    assert len(result) == 1
    # val sommato (10 + 30), label tenuta come first (no errore su stringhe)
    assert result["val"].iloc[0] == 40.0
    assert result["label"].iloc[0] in ("x", "y")


def test_dissolve_without_by_column_merges_all(gdf_two_polys):
    result = op_geo_dissolve(gdf_two_polys, "geometry", by_column=None,
                             aggfunc="first")
    assert len(result) == 1


# SIMPLIFY


def test_simplify_rejects_non_positive_tolerance(gdf_two_polys):
    with pytest.raises(ValueError, match="tolerance"):
        op_geo_simplify(gdf_two_polys, "geometry", tolerance=0)
    with pytest.raises(ValueError, match="tolerance"):
        op_geo_simplify(gdf_two_polys, "geometry", tolerance=None)


def test_simplify_reduces_vertices():
    # Linea con un vertice quasi collineare che la semplificazione rimuove
    line = LineString([(0, 0), (1, 0.0001), (2, 0), (3, 0)])
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[line], crs="EPSG:32632")
    result = op_geo_simplify(gdf, "geometry", tolerance=0.01)
    out = result.geometry.iloc[0]
    assert len(out.coords) < len(line.coords)


# CONVEX HULL / ENVELOPE / BOUNDARY


def test_convex_hull_adds_column():
    gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[MultiPoint([(0, 0), (4, 0), (2, 3), (2, 1)])],
        crs="EPSG:32632",
    )
    result = op_geo_convex_hull(gdf, "geometry", "hull")
    hull = result["hull"].iloc[0]
    assert isinstance(hull, Polygon)
    # I 3 vertici esterni formano il triangolo convesso
    assert hull.area > 0


def test_envelope_is_axis_aligned_bbox():
    poly = Polygon([(0, 0), (4, 0), (4, 2), (0, 2)])
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:32632")
    result = op_geo_envelope(gdf, "geometry", "env")
    env = result["env"].iloc[0]
    assert env.bounds == (0.0, 0.0, 4.0, 2.0)


def test_boundary_of_polygon_is_linear():
    poly = Polygon([(0, 0), (4, 0), (4, 2), (0, 2)])
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:32632")
    result = op_geo_boundary(gdf, "geometry", "bnd")
    bnd = result["bnd"].iloc[0]
    assert bnd.geom_type in ("LineString", "LinearRing", "MultiLineString")
    assert bnd.length == pytest.approx(12.0)


# EXPLODE


def test_explode_splits_multipolygon_into_rows():
    mp = MultiPolygon([
        Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
        Polygon([(5, 5), (6, 5), (6, 6), (5, 6)]),
    ])
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[mp], crs="EPSG:32632")
    result = op_geo_explode(gdf, "geometry")
    assert len(result) == 2
    assert all(g.geom_type == "Polygon" for g in result.geometry)
    # Attributi replicati su entrambe le parti
    assert list(result["id"]) == [1, 1]


# MAKE VALID


def test_make_valid_noop_when_all_valid(gdf_two_polys):
    result = op_geo_make_valid(gdf_two_polys, "geometry")
    assert len(result) == len(gdf_two_polys)
    assert bool(result.geometry.is_valid.all())


def test_make_valid_repairs_self_intersection():
    # Bow-tie: poligono auto-intersecante non valido
    bowtie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2)])
    assert not bowtie.is_valid
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[bowtie], crs="EPSG:32632")
    result = op_geo_make_valid(gdf, "geometry")
    assert bool(result.geometry.is_valid.all())


# WITHIN


def test_within_filters_contained_points():
    container = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    pts = gpd.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[Point(5, 5), Point(50, 50)],
        crs="EPSG:32632",
    )
    poly = gpd.GeoDataFrame({"zone": ["Z"]}, geometry=[container],
                            crs="EPSG:32632")
    result = op_geo_within([pts, poly], "geometry", "geometry")
    # Solo il punto interno sopravvive; nessuna colonna del destro
    assert len(result) == 1
    assert result["id"].iloc[0] == 1
    assert "zone" not in result.columns


def test_within_single_input_returns_first():
    pts = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(5, 5)], crs="EPSG:32632",
    )
    result = op_geo_within([pts], "geometry", "geometry")
    assert result is pts


# POINT ON SURFACE


def test_point_on_surface_inside_concave_polygon():
    # L-shape concava: il centroide potrebbe cadere fuori, il rep point no
    lshape = Polygon([(0, 0), (4, 0), (4, 1), (1, 1), (1, 4), (0, 4)])
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[lshape], crs="EPSG:32632")
    result = op_geo_point_on_surface(gdf, "geometry", "pos")
    pos = result["pos"].iloc[0]
    assert isinstance(pos, Point)
    assert lshape.contains(pos) or lshape.touches(pos)


# VORONOI


def test_voronoi_generates_one_cell_per_point(gdf_points):
    result = op_geo_voronoi(gdf_points, "geometry")
    assert len(result) == len(gdf_points)
    assert all(g.geom_type in ("Polygon", "MultiPolygon")
               for g in result.geometry)


def test_voronoi_cap_exceeded_raises(monkeypatch, gdf_points):
    monkeypatch.setenv("PLENORA_GEO_VORONOI_MAX_POINTS", "2")
    with pytest.raises(ValueError, match="supera il cap"):
        op_geo_voronoi(gdf_points, "geometry")


# TO WKT


def test_to_wkt_mode_wkt_adds_text_column():
    gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(11.34, 44.49)], crs="EPSG:4326",
    )
    result = op_geo_to_wkt(gdf, "geometry", mode="wkt", wkt_column="w")
    assert "w" in result.columns
    assert result["w"].iloc[0].startswith("POINT")


def test_to_wkt_mode_latlon_adds_coords():
    gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(11.34, 44.49)], crs="EPSG:4326",
    )
    result = op_geo_to_wkt(gdf, "geometry", mode="latlon",
                           lat_column="la", lon_column="lo")
    assert result["lo"].iloc[0] == pytest.approx(11.34)
    assert result["la"].iloc[0] == pytest.approx(44.49)


def test_to_wkt_mode_both_adds_all():
    gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(11.34, 44.49)], crs="EPSG:4326",
    )
    result = op_geo_to_wkt(gdf, "geometry", mode="both",
                           wkt_column="w", lat_column="la", lon_column="lo")
    assert {"w", "la", "lo"}.issubset(result.columns)


# CLEAN TOPOLOGY


def test_clean_topology_removes_overlap():
    # Due poligoni che si sovrappongono: la sovrapposizione viene sottratta
    p1 = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)])
    p2 = Polygon([(2, 0), (6, 0), (6, 4), (2, 4)])
    gdf = gpd.GeoDataFrame({"id": [1, 2]}, geometry=[p1, p2],
                           crs="EPSG:32632")
    result = op_geo_clean_topology(
        gdf, "geometry", snap_tolerance=0, remove_overlaps=True,
        fill_gaps=False,
    )
    # Le aree totali non si sovrappongono piu' (somma ~ unione)
    total_area = result.geometry.area.sum()
    union_area = result.geometry.unary_union.area
    assert total_area == pytest.approx(union_area, rel=1e-6)


def test_clean_topology_cap_geoms_raises(monkeypatch):
    monkeypatch.setenv("PLENORA_GEO_CLEAN_TOPOLOGY_MAX_GEOMS", "1")
    p1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    p2 = Polygon([(2, 0), (3, 0), (3, 1), (2, 1)])
    gdf = gpd.GeoDataFrame({"id": [1, 2]}, geometry=[p1, p2],
                           crs="EPSG:32632")
    with pytest.raises(ValueError, match="geometrie supera il cap"):
        op_geo_clean_topology(gdf, "geometry")


def test_clean_topology_cap_vertices_raises(monkeypatch):
    monkeypatch.setenv("PLENORA_GEO_CLEAN_TOPOLOGY_MAX_VERTICES", "3")
    p1 = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)])
    p2 = Polygon([(5, 0), (9, 0), (9, 4), (5, 4)])
    gdf = gpd.GeoDataFrame({"id": [1, 2]}, geometry=[p1, p2],
                           crs="EPSG:32632")
    with pytest.raises(ValueError, match="vertici totali supera"):
        op_geo_clean_topology(gdf, "geometry")


# BOUNDS EXTRACTOR / VERTEX COUNT


def test_bounds_extractor_with_prefix():
    poly = Polygon([(1, 2), (5, 2), (5, 8), (1, 8)])
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:32632")
    result = op_geo_bounds_extractor(gdf, "geometry", prefix="bbox_")
    assert result["bbox_min_x"].iloc[0] == 1.0
    assert result["bbox_min_y"].iloc[0] == 2.0
    assert result["bbox_max_x"].iloc[0] == 5.0
    assert result["bbox_max_y"].iloc[0] == 8.0


def test_vertex_count_counts_coordinates():
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])  # 5 coord (chiusa)
    pt = Point(3, 3)
    gdf = gpd.GeoDataFrame({"id": [1, 2]}, geometry=[poly, pt],
                           crs="EPSG:32632")
    result = op_geo_vertex_count(gdf, "geometry", "vc")
    assert result["vc"].iloc[0] == 5
    assert result["vc"].iloc[1] == 1


# REPROJECT


def test_reproject_changes_crs():
    gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(11.34, 44.49)], crs="EPSG:4326",
    )
    result = op_geo_reproject(gdf, "geometry", target_crs="EPSG:32632")
    assert str(result.crs) == "EPSG:32632"
    assert result.geometry.iloc[0].x > 100000


def test_reproject_source_crs_override():
    # GDF senza CRS dichiarato: source_crs lo fornisce
    gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(11.34, 44.49)],
    )
    result = op_geo_reproject(
        gdf, "geometry", target_crs="EPSG:32632", source_crs="EPSG:4326",
    )
    assert str(result.crs) == "EPSG:32632"


def test_reproject_fails_loud_without_any_crs():
    # Input DataFrame con WKT-like ma nessun CRS rilevabile -> ValueError
    df = pd.DataFrame({"geometry": ["POINT (11.34 44.49)"], "id": [1]})
    with pytest.raises(ValueError, match="source_crs non fornito"):
        op_geo_reproject(df, "geometry", target_crs="EPSG:32632")


# DISTANCE / NEAREST edge branches


def test_distance_single_input_returns_it():
    left = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(0, 0)], crs="EPSG:32632",
    )
    result = op_geo_distance([left], "geometry", "geometry")
    assert result is left


def test_distance_empty_right_sets_none():
    left = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(0, 0)], crs="EPSG:32632",
    )
    right = gpd.GeoDataFrame({"id": []}, geometry=[], crs="EPSG:32632")
    result = op_geo_distance([left, right], "geometry", "geometry", "d")
    assert result["d"].iloc[0] is None


def test_nearest_empty_input_returns_left():
    left = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(0, 0)], crs="EPSG:32632",
    )
    right = gpd.GeoDataFrame({"id": []}, geometry=[], crs="EPSG:32632")
    result = op_geo_nearest([left, right], "geometry", "geometry")
    assert len(result) == 1
    assert result["id"].iloc[0] == 1
