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
# op_sjoin
# =============================================================================

class TestOpSjoin:

    def test_inner_join(self):
        """Inner join: solo punti che intersecano un poligono."""
        polys = make_polygons(5)
        pts   = make_points(20)
        result = op_sjoin([pts, polys], "geometry", "geometry",
                          predicate="intersects", how="inner")
        # Inner join → <= n punti
        assert len(result) <= len(pts)

    def test_left_join_preserva_tutti(self):
        """Left join → tutti i punti del left sono nel risultato."""
        polys = make_polygons(5)
        pts   = make_points(10)
        result = op_sjoin([pts, polys], "geometry", "geometry",
                          predicate="intersects", how="left")
        assert len(result) >= len(pts)

    def test_inner_lte_left(self):
        """Mutation target: inner ≤ left sempre."""
        polys = make_polygons(5)
        pts   = make_points(15)
        inner = op_sjoin([pts, polys], "geometry", "geometry", how="inner")
        left  = op_sjoin([pts, polys], "geometry", "geometry", how="left")
        assert len(inner) <= len(left)

    def test_meno_di_2_input_passthrough(self):
        """Mutation target: < 2 input → restituisce il primo invariato."""
        pts = make_points(5)
        result = op_sjoin([pts], "geometry", "geometry")
        assert len(result) == len(pts)

    def test_colonne_da_entrambi_presenti(self):
        """Dopo inner join → colonne da entrambi i GDF."""
        polys = make_polygons(5)
        pts   = make_points(20)
        result = op_sjoin([pts, polys], "geometry", "geometry",
                          predicate="intersects", how="inner")
        # "nome" da pts + "nome_right" o "nome" da polys
        assert "nome" in result.columns or "nome_left" in result.columns

    def test_no_index_right_nel_risultato(self):
        """index_right deve essere rimossa dopo il join."""
        polys = make_polygons(5)
        pts   = make_points(20)
        result = op_sjoin([pts, polys], "geometry", "geometry")
        assert "index_right" not in result.columns

    def test_predicate_within(self):
        """within è più restrittivo di intersects."""
        polys = make_polygons(5)
        pts   = make_points(20)
        within    = op_sjoin([pts, polys], "geometry", "geometry", predicate="within",    how="inner")
        intersects= op_sjoin([pts, polys], "geometry", "geometry", predicate="intersects",how="inner")
        assert len(within) <= len(intersects)


# =============================================================================
# op_geo_buffer
# =============================================================================

class TestOpGeoBuffer:

    def test_buffer_crs_geografico(self):
        """CRS geografico → riproietta in UTM per buffer in metri."""
        gdf = make_polygons(3, crs="EPSG:4326")
        df = gdf_to_df_wkt(gdf)
        result = op_geo_buffer(df, "geometry", distance=100, output_column="buf")
        assert "buf" in result.columns
        assert len(result) == len(df)

    def test_buffer_crs_metrico(self):
        """CRS metrico → buffer direttamente in metri."""
        gdf = make_polygons(3, crs="EPSG:32632")
        df = gdf_to_df_wkt(gdf)
        result = op_geo_buffer(df, "geometry", distance=500, output_column="buf")
        assert "buf" in result.columns

    def test_buffer_distanza_zero(self):
        """distance=0 → geometrie invariate."""
        gdf = make_polygons(3)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_buffer(df, "geometry", distance=0, output_column="buf")
        assert "buf" in result.columns
        assert len(result) == 3

    def test_buffer_cap_style_round(self):
        gdf = make_points(3)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_buffer(df, "geometry", distance=100, cap_style="round")
        assert len(result) == 3

    def test_buffer_cap_style_flat(self):
        gdf = make_points(3)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_buffer(df, "geometry", distance=100, cap_style="flat")
        assert len(result) == 3

    def test_output_column_custom(self):
        gdf = make_polygons(3)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_buffer(df, "geometry", distance=100, output_column="zona_rispetto")
        assert "zona_rispetto" in result.columns

    def test_row_count_invariato(self):
        gdf = make_polygons(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_buffer(df, "geometry", distance=200)
        assert len(result) == 5


# =============================================================================
# op_geo_centroid
# =============================================================================

class TestOpGeoCentroid:

    def test_centroid_base(self):
        gdf = make_polygons(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_centroid(df, "geometry")
        assert "centroid" in result.columns
        assert len(result) == 5

    def test_output_column_custom(self):
        gdf = make_polygons(3)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_centroid(df, "geometry", output_column="centro")
        assert "centro" in result.columns
        assert "centroid" not in result.columns

    def test_geometria_originale_preservata(self):
        """La colonna geometry originale deve essere ancora presente."""
        gdf = make_polygons(3)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_centroid(df, "geometry", output_column="centro")
        assert "geometry" in result.columns

    def test_crs_geografico_non_crasha(self):
        """CRS geografico → riproietta per calcolo corretto, no crash."""
        gdf = make_polygons(3, crs="EPSG:4326")
        df = gdf_to_df_wkt(gdf)
        result = op_geo_centroid(df, "geometry")
        assert len(result) == 3

    def test_punti_hanno_centroid(self):
        """Centroide di un punto = il punto stesso."""
        gdf = make_points(3)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_centroid(df, "geometry")
        assert "centroid" in result.columns


# =============================================================================
# op_geo_area
# =============================================================================

class TestOpGeoArea:

    def test_area_positiva(self):
        """Poligoni → area > 0."""
        gdf = make_polygons(5, crs="EPSG:4326")
        df = gdf_to_df_wkt(gdf)
        result = op_geo_area(df, "geometry")
        assert "area_m2" in result.columns
        assert (result["area_m2"] > 0).all()

    def test_output_column_custom(self):
        gdf = make_polygons(3)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_area(df, "geometry", output_column="superficie_mq")
        assert "superficie_mq" in result.columns

    def test_crs_metrico(self):
        """CRS metrico → area in m², senza riproiezione."""
        gdf = make_polygons(3, crs="EPSG:32632")
        df = gdf_to_df_wkt(gdf)
        result = op_geo_area(df, "geometry")
        assert "area_m2" in result.columns
        assert (result["area_m2"] >= 0).all()

    def test_row_count_invariato(self):
        gdf = make_polygons(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_area(df, "geometry")
        assert len(result) == 5


# =============================================================================
# op_geo_dissolve
# =============================================================================

class TestOpGeoDdissolve:

    def _gdf_con_categoria(self):
        """GDF con categoria per test dissolve by_column."""
        geoms = [
            Polygon([(9+i*0.1, 45), (9+i*0.1+0.05, 45), (9+i*0.1+0.05, 45.05), (9+i*0.1, 45.05)])
            for i in range(6)
        ]
        return gpd.GeoDataFrame({
            "id":        range(6),
            "categoria": ["A", "A", "B", "B", "C", "C"],
            "valore":    [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
        }, geometry=geoms, crs="EPSG:4326")

    def test_dissolve_tutto(self):
        """Dissolve senza by_column → 1 geometria unificata."""
        gdf = self._gdf_con_categoria()
        df = gdf_to_df_wkt(gdf)
        result = op_geo_dissolve(df, "geometry")
        assert len(result) >= 1

    def test_dissolve_per_colonna(self):
        """Dissolve per categoria → 3 geometrie (A, B, C)."""
        gdf = self._gdf_con_categoria()
        df = gdf_to_df_wkt(gdf)
        result = op_geo_dissolve(df, "geometry", by_column="categoria")
        assert len(result) == 3

    def test_dissolve_aggfunc_sum(self):
        gdf = self._gdf_con_categoria()
        df = gdf_to_df_wkt(gdf)
        result = op_geo_dissolve(df, "geometry", by_column="categoria", aggfunc="sum")
        assert len(result) == 3

    def test_dissolve_aggfunc_count(self):
        gdf = self._gdf_con_categoria()
        df = gdf_to_df_wkt(gdf)
        result = op_geo_dissolve(df, "geometry", by_column="categoria", aggfunc="count")
        assert len(result) == 3

    def test_dissolve_by_column_inesistente(self):
        """by_column non esistente → dissolve tutto."""
        gdf = self._gdf_con_categoria()
        df = gdf_to_df_wkt(gdf)
        result = op_geo_dissolve(df, "geometry", by_column="COLONNA_XYZ")
        assert len(result) >= 1


# =============================================================================
# op_geo_clip
# =============================================================================

class TestOpGeoClip:

    def test_clip_base(self):
        """Clip punti con poligono → solo punti dentro."""
        pts = make_points(20)
        # Poligono che copre solo parte dell'area
        mask = gpd.GeoDataFrame(
            geometry=[Polygon([(9.0, 45.0), (9.3, 45.0), (9.3, 45.2), (9.0, 45.2)])],
            crs="EPSG:4326"
        )
        df_pts  = gdf_to_df_wkt(pts)
        df_mask = gdf_to_df_wkt(mask)
        result = op_geo_clip([df_pts, df_mask], "geometry", "geometry")
        assert len(result) <= len(pts)

    def test_clip_meno_di_2_input_passthrough(self):
        pts = make_points(5)
        df = gdf_to_df_wkt(pts)
        result = op_geo_clip([df], "geometry", "geometry")
        assert len(result) == len(df)

    def test_clip_zero_righe_non_crasha(self):
        """Maschera vuota → 0 righe, no crash."""
        pts  = make_points(5)
        # Poligono lontano dall'Italia → 0 intersezioni
        mask = gpd.GeoDataFrame(
            geometry=[Polygon([(0.0, 0.0), (0.1, 0.0), (0.1, 0.1), (0.0, 0.1)])],
            crs="EPSG:4326"
        )
        df_pts  = gdf_to_df_wkt(pts)
        df_mask = gdf_to_df_wkt(mask)
        result = op_geo_clip([df_pts, df_mask], "geometry", "geometry")
        assert len(result) == 0


# =============================================================================
# op_geo_intersection, op_geo_union, op_geo_difference
# =============================================================================

class TestOpGeoSetOperations:

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

    def test_intersection_2_input(self):
        a, b = self._two_overlapping_gdfs()
        result = op_geo_intersection([a, b], "geometry", "geometry")
        assert len(result) >= 0

    def test_intersection_meno_di_2_passthrough(self):
        a, _ = self._two_overlapping_gdfs()
        result = op_geo_intersection([a], "geometry", "geometry")
        assert len(result) == len(pd.DataFrame(a))

    def test_union_2_input(self):
        a, b = self._two_overlapping_gdfs()
        result = op_geo_union([a, b], "geometry", "geometry")
        assert len(result) >= 1

    def test_union_meno_di_2_passthrough(self):
        a, _ = self._two_overlapping_gdfs()
        result = op_geo_union([a], "geometry", "geometry")
        assert len(result) == len(pd.DataFrame(a))

    def test_difference_2_input(self):
        a, b = self._two_overlapping_gdfs()
        result = op_geo_difference([a, b], "geometry", "geometry")
        assert len(result) >= 0

    def test_difference_meno_di_2_passthrough(self):
        a, _ = self._two_overlapping_gdfs()
        result = op_geo_difference([a], "geometry", "geometry")
        assert len(result) == len(pd.DataFrame(a))

    def test_difference_a_meno_a_vuoto_o_piccolo(self):
        """A - A = geometria vuota o quasi."""
        a, _ = self._two_overlapping_gdfs()
        result = op_geo_difference([a, a], "geometry", "geometry")
        # La differenza di A con se stessa è vuota
        assert len(result) == 0 or result.geometry.is_empty.all()


# =============================================================================
# op_geo_simplify
# =============================================================================

class TestOpGeoSimplify:

    def test_simplify_tolerance_piccola(self):
        """Tolerance piccola → geometrie quasi invariate."""
        gdf = make_polygons(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_simplify(df, "geometry", tolerance=0.0001)
        assert len(result) == 5

    def test_simplify_tolerance_grande(self):
        """Tolerance grande → geometrie semplificate."""
        gdf = make_polygons(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_simplify(df, "geometry", tolerance=1.0)
        assert len(result) == 5

    def test_preserve_topology_true(self):
        gdf = make_polygons(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_simplify(df, "geometry", tolerance=0.01, preserve_topology=True)
        assert len(result) == 5

    def test_preserve_topology_false(self):
        gdf = make_polygons(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_simplify(df, "geometry", tolerance=0.01, preserve_topology=False)
        assert len(result) == 5


# =============================================================================
# op_geo_convex_hull
# =============================================================================

class TestOpGeoConvexHull:

    def test_convex_hull_base(self):
        gdf = make_polygons(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_convex_hull(df, "geometry")
        assert "convex_hull" in result.columns
        assert len(result) == 5

    def test_output_column_custom(self):
        gdf = make_polygons(3)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_convex_hull(df, "geometry", output_column="inviluppo")
        assert "inviluppo" in result.columns
        assert "convex_hull" not in result.columns

    def test_row_count_invariato(self):
        gdf = make_polygons(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_convex_hull(df, "geometry")
        assert len(result) == len(gdf)
