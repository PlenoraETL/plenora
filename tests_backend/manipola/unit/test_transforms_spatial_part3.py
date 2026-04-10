"""
tests/manipola/unit/test_transforms_spatial.py
===============================================
Unit test diretti sulle funzioni op_geo_* e op_sjoin di transforms/spatial.py.

NON richiede backend, API o Celery.
Usa GeoDataFrame sintetici costruiti in-process con shapely e geopandas.

Obiettivi coverage:
  - op_sjoin: inner/left, predicate, < 2 input
  - op_geo_buffer: CRS geografico→UTM, metrico, distanza=0, cap_style
  - op_geo_centroid: output_column custom, CRS geografico/metrico
  - op_geo_area: CRS geografico→UTM, metrico, output_column custom
  - op_geo_dissolve: by_column presente/assente, aggfunc first/sum/mean/count
  - op_geo_clip: 2 input, CRS diversi, < 2 input
  - op_geo_intersection/union/difference: 2 input, < 2 input
  - op_geo_simplify: tolerance, preserve_topology True/False
  - op_geo_convex_hull: output_column custom
  - op_geo_reproject: 4326→32632, source_crs esplicito
  - op_geo_envelope: output_column custom
"""

import pytest
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, Polygon, MultiPolygon, LineString

pytestmark = pytest.mark.unit

from app.core.manipola.transforms.spatial import (
    op_sjoin,
    op_geo_buffer,
    op_geo_centroid,
    op_geo_area,
    op_geo_dissolve,
    op_geo_clip,
    op_geo_intersection,
    op_geo_union,
    op_geo_difference,
    op_geo_simplify,
    op_geo_convex_hull,
    op_geo_reproject,
    op_geo_envelope,
    op_geo_distance,
    op_geo_nearest,
    op_geo_length,
    op_geo_perimeter,
    op_geo_explode,
    op_geo_make_valid,
    op_geo_within,
    op_geo_point_on_surface,
    op_geo_boundary,
    op_geo_voronoi,
    op_geo_to_wkt,
    op_geo_symmetric_difference,
    op_geo_count_points_in_polygons,
    op_geo_clean_topology,
    op_geo_overlay,
    op_geo_bounds_extractor,
    op_geo_vertex_count,
    op_geo_line_builder,
    op_geo_polygon_builder,
)


# ─── Helper: GeoDataFrame sintetici ──────────────────────────────────────────

def make_polygons(n=5, crs="EPSG:4326"):
    """N poligoni sintetici in Italia (area nord)."""
    geoms = []
    for i in range(n):
        lon = 9.0 + i * 0.1
        lat = 45.0 + i * 0.05
        geoms.append(Polygon([
            (lon, lat), (lon+0.05, lat), (lon+0.05, lat+0.05),
            (lon, lat+0.05), (lon, lat)
        ]))
    return gpd.GeoDataFrame(
        {"id": range(n), "nome": [f"Poligono_{i}" for i in range(n)],
         "valore": [float(i*100) for i in range(n)]},
        geometry=geoms, crs=crs
    )


def make_points(n=10, crs="EPSG:4326"):
    """N punti sintetici in Italia."""
    geoms = [Point(9.0 + i*0.05, 45.0 + i*0.025) for i in range(n)]
    return gpd.GeoDataFrame(
        {"id": range(n), "nome": [f"Punto_{i}" for i in range(n)]},
        geometry=geoms, crs=crs
    )


def gdf_to_df_wkt(gdf):
    """Converte GeoDataFrame in DataFrame con colonna geometry come WKT."""
    df = gdf.copy()
    df["geometry"] = gdf.geometry.to_wkt()
    return pd.DataFrame(df)




# =============================================================================
# op_geo_voronoi
# =============================================================================

class TestOpGeoVoronoi:

    def test_voronoi_from_points(self):
        """Voronoi da punti → stesso numero di righe dell'input."""
        pts = make_points(5)
        df = gdf_to_df_wkt(pts)
        result = op_geo_voronoi(df, "geometry")
        assert len(result) == len(pts)

    def test_output_is_polygon(self):
        """Le celle Voronoi devono essere poligoni."""
        pts = make_points(5)
        df = gdf_to_df_wkt(pts)
        result = op_geo_voronoi(df, "geometry")
        if isinstance(result, gpd.GeoDataFrame):
            assert all(
                g.geom_type in ("Polygon", "MultiPolygon")
                for g in result.geometry if not g.is_empty
            )


# =============================================================================
# op_geo_to_wkt
# =============================================================================

class TestOpGeoToWkt:

    def test_mode_wkt(self):
        """Modalita wkt → colonna wkt presente."""
        gdf = make_points(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_to_wkt(df, "geometry", mode="wkt")
        wkt_cols = [c for c in result.columns if "wkt" in c.lower()]
        assert len(wkt_cols) >= 1

    def test_mode_latlon(self):
        """Modalita latlon → colonne lat e lon presenti."""
        gdf = make_points(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_to_wkt(df, "geometry", mode="latlon")
        cols_lower = [c.lower() for c in result.columns]
        assert any("lat" in c for c in cols_lower)
        assert any("lon" in c for c in cols_lower)

    def test_mode_both(self):
        """Modalita both → colonne wkt, lat e lon tutte presenti."""
        gdf = make_points(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_to_wkt(df, "geometry", mode="both")
        cols_lower = [c.lower() for c in result.columns]
        assert any("wkt" in c for c in cols_lower)
        assert any("lat" in c for c in cols_lower)
        assert any("lon" in c for c in cols_lower)


# =============================================================================
# op_geo_symmetric_difference
# =============================================================================

class TestOpGeoSymmetricDifference:

    def _two_overlapping_gdfs(self):
        poly_a = gpd.GeoDataFrame(
            geometry=[Polygon([(9.0, 45.0), (9.2, 45.0), (9.2, 45.2), (9.0, 45.2)])],
            crs="EPSG:4326"
        )
        poly_b = gpd.GeoDataFrame(
            geometry=[Polygon([(9.1, 45.1), (9.3, 45.1), (9.3, 45.3), (9.1, 45.3)])],
            crs="EPSG:4326"
        )
        return gdf_to_df_wkt(poly_a), gdf_to_df_wkt(poly_b)

    def test_2_input(self):
        """Symmetric difference di due poligoni sovrapposti → risultato non vuoto."""
        a, b = self._two_overlapping_gdfs()
        result = op_geo_symmetric_difference([a, b], "geometry", "geometry")
        assert len(result) >= 1

    def test_meno_di_2_passthrough(self):
        """< 2 input → restituisce il primo invariato."""
        a, _ = self._two_overlapping_gdfs()
        result = op_geo_symmetric_difference([a], "geometry", "geometry")
        assert len(result) == len(pd.DataFrame(a))

    def test_non_overlapping(self):
        """Due poligoni non sovrapposti → risultato contiene entrambe le aree."""
        poly_a = gpd.GeoDataFrame(
            geometry=[Polygon([(9.0, 45.0), (9.1, 45.0), (9.1, 45.1), (9.0, 45.1)])],
            crs="EPSG:4326"
        )
        poly_b = gpd.GeoDataFrame(
            geometry=[Polygon([(10.0, 46.0), (10.1, 46.0), (10.1, 46.1), (10.0, 46.1)])],
            crs="EPSG:4326"
        )
        a = gdf_to_df_wkt(poly_a)
        b = gdf_to_df_wkt(poly_b)
        result = op_geo_symmetric_difference([a, b], "geometry", "geometry")
        assert len(result) >= 1


# =============================================================================
# op_geo_count_points_in_polygons
# =============================================================================

class TestOpGeoCountPointsInPolygons:

    def test_count_base(self):
        """Conteggio punti dentro poligoni → colonna point_count con valori >= 0."""
        polys = make_polygons(3)
        pts = make_points(10)
        poly_df = gdf_to_df_wkt(polys)
        pts_df = gdf_to_df_wkt(pts)
        result = op_geo_count_points_in_polygons(
            [poly_df, pts_df], "geometry", "geometry"
        )
        assert "point_count" in result.columns
        assert (result["point_count"] >= 0).all()

    def test_count_column_custom(self):
        """Nome personalizzato per la colonna conteggio."""
        polys = make_polygons(3)
        pts = make_points(10)
        poly_df = gdf_to_df_wkt(polys)
        pts_df = gdf_to_df_wkt(pts)
        result = op_geo_count_points_in_polygons(
            [poly_df, pts_df], "geometry", "geometry",
            count_column="n_punti"
        )
        assert "n_punti" in result.columns
        assert (result["n_punti"] >= 0).all()

    def test_aggregate_column(self):
        """Aggregazione di un attributo dei punti (sum) → colonna aggregata presente."""
        polys = make_polygons(3)
        pts = make_points(10)
        pts["valore"] = [float(i * 10) for i in range(10)]
        poly_df = gdf_to_df_wkt(polys)
        pts_df = gdf_to_df_wkt(pts)
        result = op_geo_count_points_in_polygons(
            [poly_df, pts_df], "geometry", "geometry",
            aggregate_column="valore", aggregate_func="sum"
        )
        assert "point_count" in result.columns
        # La colonna aggregata si chiama valore_sum
        agg_cols = [c for c in result.columns if "valore" in c]
        assert len(agg_cols) >= 1

    def test_meno_di_2_input_passthrough(self):
        """< 2 input → restituisce il primo invariato."""
        polys = make_polygons(3)
        poly_df = gdf_to_df_wkt(polys)
        result = op_geo_count_points_in_polygons(
            [poly_df], "geometry", "geometry"
        )
        assert len(result) == len(poly_df)


# =============================================================================
# op_geo_clean_topology
# =============================================================================

class TestOpGeoCleanTopology:

    def test_clean_base(self):
        """Poligoni validi → stesso numero, geometrie valide."""
        gdf = make_polygons(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_clean_topology(df, "geometry")
        assert len(result) == 5
        assert result.geometry.is_valid.all()

    def test_snap_closes_gaps(self):
        """Due poligoni adiacenti con micro-gap → dopo clean, topologia corretta."""
        poly_a = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        poly_b = Polygon([(1.0005, 0), (2, 0), (2, 1), (1.0005, 1)])
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2]},
            geometry=[poly_a, poly_b],
            crs="EPSG:4326"
        )
        df = gdf_to_df_wkt(gdf)
        result = op_geo_clean_topology(df, "geometry", snap_tolerance=0.01)
        assert len(result) >= 1
        assert result.geometry.is_valid.all()

    def test_row_count_invariato(self):
        """Stesso numero di righe dopo clean topology."""
        gdf = make_polygons(4)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_clean_topology(df, "geometry")
        assert len(result) == len(df)


# =============================================================================
# op_geo_overlay
# =============================================================================

class TestOpGeoOverlay:

    def _two_overlapping_gdfs(self):
        poly_a = gpd.GeoDataFrame(
            {"nome": ["A"]},
            geometry=[Polygon([(9.0, 45.0), (9.2, 45.0), (9.2, 45.2), (9.0, 45.2)])],
            crs="EPSG:4326"
        )
        poly_b = gpd.GeoDataFrame(
            {"valore": [100]},
            geometry=[Polygon([(9.1, 45.1), (9.3, 45.1), (9.3, 45.3), (9.1, 45.3)])],
            crs="EPSG:4326"
        )
        return gdf_to_df_wkt(poly_a), gdf_to_df_wkt(poly_b)

    def test_overlay_intersection(self):
        """Overlay intersection → risultato ha attributi da entrambi i layer."""
        a, b = self._two_overlapping_gdfs()
        result = op_geo_overlay([a, b], "geometry", "geometry", how="intersection")
        assert len(result) >= 1
        assert "nome" in result.columns
        assert "valore" in result.columns

    def test_overlay_union(self):
        """Overlay union → risultato ha righe."""
        a, b = self._two_overlapping_gdfs()
        result = op_geo_overlay([a, b], "geometry", "geometry", how="union")
        assert len(result) >= 1

    def test_overlay_identity(self):
        """Overlay identity → risultato ha righe."""
        a, b = self._two_overlapping_gdfs()
        result = op_geo_overlay([a, b], "geometry", "geometry", how="identity")
        assert len(result) >= 1

    def test_meno_di_2_passthrough(self):
        """< 2 input → restituisce il primo invariato."""
        a, _ = self._two_overlapping_gdfs()
        result = op_geo_overlay([a], "geometry", "geometry")
        assert len(result) == len(pd.DataFrame(a))


# =============================================================================
# op_geo_bounds_extractor — estrazione bounding box
# =============================================================================

class TestOpGeoBoundsExtractor:

    def test_bounds_base(self):
        """Estrazione bounds produce min_x, min_y, max_x, max_y."""
        gdf = make_polygons()
        df = gdf_to_df_wkt(gdf)
        result = op_geo_bounds_extractor(df, geom_column="geometry")
        for col in ("min_x", "min_y", "max_x", "max_y"):
            assert col in result.columns

    def test_prefix_custom(self):
        """Prefisso personalizzato 'bbox_' → bbox_min_x, ecc."""
        gdf = make_polygons()
        df = gdf_to_df_wkt(gdf)
        result = op_geo_bounds_extractor(df, geom_column="geometry", prefix="bbox_")
        for col in ("bbox_min_x", "bbox_min_y", "bbox_max_x", "bbox_max_y"):
            assert col in result.columns

    def test_row_count_invariato(self):
        """Il numero di righe non cambia dopo l'estrazione dei bounds."""
        gdf = make_polygons()
        df = gdf_to_df_wkt(gdf)
        result = op_geo_bounds_extractor(df, geom_column="geometry")
        assert len(result) == len(df)


# =============================================================================
# op_geo_vertex_count — conteggio vertici
# =============================================================================

class TestOpGeoVertexCount:

    def test_vertex_count_base(self):
        """I poligoni hanno vertex_count > 0."""
        gdf = make_polygons()
        df = gdf_to_df_wkt(gdf)
        result = op_geo_vertex_count(df, geom_column="geometry")
        assert "vertex_count" in result.columns
        assert all(result["vertex_count"] > 0)

    def test_output_column_custom(self):
        """Nome colonna personalizzato."""
        gdf = make_polygons()
        df = gdf_to_df_wkt(gdf)
        result = op_geo_vertex_count(df, geom_column="geometry", output_column="n_vertici")
        assert "n_vertici" in result.columns

    def test_row_count_invariato(self):
        """Il numero di righe non cambia dopo il conteggio vertici."""
        gdf = make_polygons()
        df = gdf_to_df_wkt(gdf)
        result = op_geo_vertex_count(df, geom_column="geometry")
        assert len(result) == len(df)


# =============================================================================
# op_geo_line_builder — costruzione linee da punti
# =============================================================================

class TestOpGeoLineBuilder:

    def _pts(self):
        return gpd.GeoDataFrame({
            "route": ["A", "A", "A", "B", "B", "B"],
            "seq": [1, 2, 3, 1, 2, 3],
        }, geometry=[
            Point(0, 0), Point(1, 1), Point(2, 2),
            Point(10, 10), Point(11, 11), Point(12, 12),
        ], crs="EPSG:4326")

    def test_line_builder_base(self):
        """2 gruppi → 2 righe con geometria LineString."""
        pts = self._pts()
        result = op_geo_line_builder(pts, geom_column="geometry", group_column="route")
        assert len(result) == 2
        assert all(result.geometry.geom_type == "LineString")

    def test_with_order_column(self):
        """Ordinamento esplicito per colonna seq."""
        pts = self._pts()
        result = op_geo_line_builder(pts, geom_column="geometry",
                                     group_column="route", order_column="seq")
        assert len(result) == 2
        assert all(result.geometry.geom_type == "LineString")

    def test_single_point_group_skipped(self):
        """Gruppo con 1 solo punto → skippato (servono >= 2 punti)."""
        pts = gpd.GeoDataFrame({
            "route": ["A", "B", "B"],
            "seq": [1, 1, 2],
        }, geometry=[Point(0, 0), Point(10, 10), Point(11, 11)], crs="EPSG:4326")
        result = op_geo_line_builder(pts, geom_column="geometry", group_column="route")
        # Solo il gruppo B ha >= 2 punti
        assert len(result) == 1


# =============================================================================
# op_geo_polygon_builder — costruzione poligoni da punti
# =============================================================================

class TestOpGeoPolygonBuilder:

    def _pts(self):
        return gpd.GeoDataFrame({
            "zone": ["A", "A", "A", "A", "B", "B", "B", "B"],
            "seq": [1, 2, 3, 4, 1, 2, 3, 4],
        }, geometry=[
            Point(0, 0), Point(1, 0), Point(1, 1), Point(0, 1),
            Point(10, 10), Point(11, 10), Point(11, 11), Point(10, 11),
        ], crs="EPSG:4326")

    def test_polygon_builder_base(self):
        """2 gruppi → 2 righe con geometria Polygon."""
        pts = self._pts()
        result = op_geo_polygon_builder(pts, geom_column="geometry", group_column="zone")
        assert len(result) == 2
        assert all(result.geometry.geom_type == "Polygon")

    def test_with_order_column(self):
        """Ordinamento esplicito per colonna seq."""
        pts = self._pts()
        result = op_geo_polygon_builder(pts, geom_column="geometry",
                                        group_column="zone", order_column="seq")
        assert len(result) == 2
        assert all(result.geometry.geom_type == "Polygon")

    def test_insufficient_points_skipped(self):
        """Gruppo con < 3 punti → skippato."""
        pts = gpd.GeoDataFrame({
            "zone": ["A", "A", "B", "B", "B", "B"],
            "seq": [1, 2, 1, 2, 3, 4],
        }, geometry=[
            Point(0, 0), Point(1, 0),
            Point(10, 10), Point(11, 10), Point(11, 11), Point(10, 11),
        ], crs="EPSG:4326")
        result = op_geo_polygon_builder(pts, geom_column="geometry", group_column="zone")
        # Solo il gruppo B ha >= 3 punti
        assert len(result) == 1
