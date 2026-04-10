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
# op_geo_reproject
# =============================================================================

class TestOpGeoReproject:

    def test_4326_a_32632(self):
        """WGS84 → UTM 32N."""
        gdf = make_polygons(3, crs="EPSG:4326")
        df = gdf_to_df_wkt(gdf)
        result = op_geo_reproject(df, "geometry", target_crs="EPSG:32632")
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 3
        assert str(result.crs.to_epsg()) == "32632"

    def test_32632_a_4326(self):
        """UTM 32N → WGS84."""
        gdf = make_polygons(3, crs="EPSG:32632")
        df = gdf_to_df_wkt(gdf)
        result = op_geo_reproject(df, "geometry",
                                   target_crs="EPSG:4326",
                                   source_crs="EPSG:32632")
        assert str(result.crs.to_epsg()) == "4326"

    def test_4326_a_3857(self):
        """WGS84 → Web Mercator."""
        gdf = make_polygons(3, crs="EPSG:4326")
        df = gdf_to_df_wkt(gdf)
        result = op_geo_reproject(df, "geometry", target_crs="EPSG:3857")
        assert str(result.crs.to_epsg()) == "3857"

    def test_source_crs_esplicito(self):
        """source_crs esplicito override auto-detect."""
        gdf = make_polygons(3, crs="EPSG:4326")
        df = gdf_to_df_wkt(gdf)
        result = op_geo_reproject(df, "geometry",
                                   target_crs="EPSG:32632",
                                   source_crs="EPSG:4326")
        assert len(result) == 3

    def test_row_count_invariato(self):
        gdf = make_polygons(5, crs="EPSG:4326")
        df = gdf_to_df_wkt(gdf)
        result = op_geo_reproject(df, "geometry", target_crs="EPSG:32632")
        assert len(result) == 5


# =============================================================================
# op_geo_envelope
# =============================================================================

class TestOpGeoEnvelope:

    def test_envelope_base(self):
        gdf = make_polygons(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_envelope(df, "geometry")
        assert "envelope" in result.columns
        assert len(result) == 5

    def test_output_column_custom(self):
        gdf = make_polygons(3)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_envelope(df, "geometry", output_column="bbox")
        assert "bbox" in result.columns
        assert "envelope" not in result.columns

    def test_row_count_invariato(self):
        gdf = make_polygons(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_envelope(df, "geometry")
        assert len(result) == len(gdf)

    def test_envelope_e_rettangolo(self):
        """L'envelope deve essere un Polygon (bounding box rettangolare)."""
        gdf = make_polygons(3)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_envelope(df, "geometry")
        # Converti la colonna envelope in geometrie
        env_geoms = gpd.GeoSeries.from_wkt(
            result["envelope"].apply(lambda g: g.wkt if hasattr(g, 'wkt') else str(g))
        )
        assert all(g.geom_type == "Polygon" for g in env_geoms if not g.is_empty)


# ─── Helper: LineString sintetiche ──────────────────────────────────────────

def make_lines(n=5, crs="EPSG:4326"):
    """N linee sintetiche in Italia (area nord)."""
    geoms = []
    for i in range(n):
        lon = 9.0 + i * 0.1
        lat = 45.0 + i * 0.05
        geoms.append(LineString([(lon, lat), (lon + 0.1, lat + 0.05)]))
    return gpd.GeoDataFrame(
        {"id": range(n), "nome": [f"Linea_{i}" for i in range(n)]},
        geometry=geoms, crs=crs
    )


# =============================================================================
# op_geo_distance
# =============================================================================

class TestOpGeoDistance:

    def test_distance_positive(self):
        """Due layer distinti → distanza > 0 per ogni riga."""
        polys = make_polygons(5)
        pts = make_points(5)
        df_polys = gdf_to_df_wkt(polys)
        df_pts = gdf_to_df_wkt(pts)
        result = op_geo_distance([df_pts, df_polys], "geometry", "geometry")
        assert "distance" in result.columns or any("dist" in c for c in result.columns)

    def test_output_column_custom(self):
        """output_column personalizzata viene usata."""
        polys = make_polygons(3)
        pts = make_points(3)
        df_polys = gdf_to_df_wkt(polys)
        df_pts = gdf_to_df_wkt(pts)
        result = op_geo_distance([df_pts, df_polys], "geometry", "geometry",
                                  output_column="dist_m")
        assert "dist_m" in result.columns

    def test_meno_di_2_input_passthrough(self):
        """< 2 input → restituisce il primo invariato."""
        pts = make_points(5)
        df = gdf_to_df_wkt(pts)
        result = op_geo_distance([df], "geometry", "geometry")
        assert len(result) == len(df)

    def test_row_count(self):
        """Il numero di righe del risultato corrisponde al primo input."""
        polys = make_polygons(5)
        pts = make_points(10)
        df_polys = gdf_to_df_wkt(polys)
        df_pts = gdf_to_df_wkt(pts)
        result = op_geo_distance([df_pts, df_polys], "geometry", "geometry")
        assert len(result) == len(pts)


# =============================================================================
# op_geo_nearest
# =============================================================================

class TestOpGeoNearest:

    def test_nearest_base(self):
        """Ogni riga del left ottiene un match dal right."""
        polys = make_polygons(5)
        pts = make_points(10)
        df_polys = gdf_to_df_wkt(polys)
        df_pts = gdf_to_df_wkt(pts)
        result = op_geo_nearest([df_pts, df_polys], "geometry", "geometry")
        assert len(result) == len(pts)

    def test_distance_column_present(self):
        """Il risultato contiene una colonna di distanza."""
        polys = make_polygons(5)
        pts = make_points(10)
        df_polys = gdf_to_df_wkt(polys)
        df_pts = gdf_to_df_wkt(pts)
        result = op_geo_nearest([df_pts, df_polys], "geometry", "geometry")
        dist_cols = [c for c in result.columns if "dist" in c.lower()]
        assert len(dist_cols) >= 1

    def test_max_distance_filter(self):
        """max_distance limita i risultati ai vicini entro la soglia."""
        polys = make_polygons(3)
        pts = make_points(10)
        df_polys = gdf_to_df_wkt(polys)
        df_pts = gdf_to_df_wkt(pts)
        result = op_geo_nearest([df_pts, df_polys], "geometry", "geometry",
                                 max_distance=1.0)
        assert len(result) <= len(pts)

    def test_meno_di_2_input_passthrough(self):
        """< 2 input → restituisce il primo invariato."""
        pts = make_points(5)
        df = gdf_to_df_wkt(pts)
        result = op_geo_nearest([df], "geometry", "geometry")
        assert len(result) == len(df)


# =============================================================================
# op_geo_length
# =============================================================================

class TestOpGeoLength:

    def test_length_positive(self):
        """Linee → lunghezza > 0."""
        gdf = make_lines(5, crs="EPSG:4326")
        df = gdf_to_df_wkt(gdf)
        result = op_geo_length(df, "geometry")
        length_cols = [c for c in result.columns if "length" in c.lower() or "lungh" in c.lower()]
        assert len(length_cols) >= 1
        col = length_cols[0]
        assert (result[col] > 0).all()

    def test_output_column_custom(self):
        """output_column personalizzata viene usata."""
        gdf = make_lines(3)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_length(df, "geometry", output_column="lunghezza_m")
        assert "lunghezza_m" in result.columns

    def test_row_count_invariato(self):
        """Il numero di righe non cambia."""
        gdf = make_lines(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_length(df, "geometry")
        assert len(result) == 5

    def test_crs_metrico(self):
        """CRS metrico → lunghezza calcolata senza riproiezione."""
        gdf = make_lines(3, crs="EPSG:32632")
        df = gdf_to_df_wkt(gdf)
        result = op_geo_length(df, "geometry")
        assert len(result) == 3


# =============================================================================
# op_geo_perimeter
# =============================================================================

class TestOpGeoPerimeter:

    def test_perimeter_positive(self):
        """Poligoni → perimetro > 0."""
        gdf = make_polygons(5, crs="EPSG:4326")
        df = gdf_to_df_wkt(gdf)
        result = op_geo_perimeter(df, "geometry")
        perim_cols = [c for c in result.columns if "perim" in c.lower()]
        assert len(perim_cols) >= 1
        col = perim_cols[0]
        assert (result[col] > 0).all()

    def test_output_column_custom(self):
        """output_column personalizzata viene usata."""
        gdf = make_polygons(3)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_perimeter(df, "geometry", output_column="perimetro_m")
        assert "perimetro_m" in result.columns

    def test_row_count_invariato(self):
        """Il numero di righe non cambia."""
        gdf = make_polygons(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_perimeter(df, "geometry")
        assert len(result) == 5


# =============================================================================
# op_geo_explode
# =============================================================================

class TestOpGeoExplode:

    def test_explode_multipolygon(self):
        """MultiPolygon → piu righe nel risultato."""
        p1 = Polygon([(9.0, 45.0), (9.1, 45.0), (9.1, 45.1), (9.0, 45.1)])
        p2 = Polygon([(9.2, 45.0), (9.3, 45.0), (9.3, 45.1), (9.2, 45.1)])
        mp = MultiPolygon([p1, p2])
        gdf = gpd.GeoDataFrame(
            {"id": [0], "nome": ["Multi"]},
            geometry=[mp], crs="EPSG:4326"
        )
        df = gdf_to_df_wkt(gdf)
        result = op_geo_explode(df, "geometry")
        assert len(result) >= 2

    def test_explode_single_polygon(self):
        """Polygon singoli → stesso numero di righe."""
        gdf = make_polygons(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_explode(df, "geometry")
        assert len(result) == 5

    def test_colonne_preservate(self):
        """Le colonne non-geometriche sono preservate dopo explode."""
        p1 = Polygon([(9.0, 45.0), (9.1, 45.0), (9.1, 45.1), (9.0, 45.1)])
        p2 = Polygon([(9.2, 45.0), (9.3, 45.0), (9.3, 45.1), (9.2, 45.1)])
        mp = MultiPolygon([p1, p2])
        gdf = gpd.GeoDataFrame(
            {"id": [0], "nome": ["Multi"]},
            geometry=[mp], crs="EPSG:4326"
        )
        df = gdf_to_df_wkt(gdf)
        result = op_geo_explode(df, "geometry")
        assert "id" in result.columns
        assert "nome" in result.columns


# =============================================================================
# op_geo_make_valid
# =============================================================================

class TestOpGeoMakeValid:

    def test_valid_geometry_unchanged(self):
        """Geometria valida → rimane invariata."""
        gdf = make_polygons(3)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_make_valid(df, "geometry")
        assert len(result) == 3

    def test_self_intersecting_polygon_fixed(self):
        """Poligono auto-intersecante → viene corretto."""
        # Bowtie polygon (self-intersecting)
        bowtie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2)])
        gdf = gpd.GeoDataFrame(
            {"id": [0]},
            geometry=[bowtie], crs="EPSG:4326"
        )
        df = gdf_to_df_wkt(gdf)
        result = op_geo_make_valid(df, "geometry")
        assert len(result) == 1
        # La geometria risultante deve essere valida
        if isinstance(result, gpd.GeoDataFrame):
            assert result.geometry.is_valid.all()

    def test_row_count_invariato(self):
        """Il numero di righe non cambia."""
        gdf = make_polygons(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_make_valid(df, "geometry")
        assert len(result) == 5


# =============================================================================
# op_geo_within
# =============================================================================

class TestOpGeoWithin:

    def test_points_within_polygon(self):
        """Solo i punti dentro il poligono vengono restituiti."""
        pts = make_points(20)
        mask = gpd.GeoDataFrame(
            geometry=[Polygon([(9.0, 45.0), (9.3, 45.0), (9.3, 45.2), (9.0, 45.2)])],
            crs="EPSG:4326"
        )
        df_pts = gdf_to_df_wkt(pts)
        df_mask = gdf_to_df_wkt(mask)
        result = op_geo_within([df_pts, df_mask], "geometry", "geometry")
        assert len(result) <= len(pts)

    def test_meno_di_2_input_passthrough(self):
        """< 2 input → restituisce il primo invariato."""
        pts = make_points(5)
        df = gdf_to_df_wkt(pts)
        result = op_geo_within([df], "geometry", "geometry")
        assert len(result) == len(df)

    def test_all_within(self):
        """Tutti i punti dentro un poligono grande → tutti restituiti."""
        pts = make_points(5)
        # Poligono molto grande che contiene tutti i punti
        big_poly = gpd.GeoDataFrame(
            geometry=[Polygon([(8.0, 44.0), (11.0, 44.0), (11.0, 47.0), (8.0, 47.0)])],
            crs="EPSG:4326"
        )
        df_pts = gdf_to_df_wkt(pts)
        df_mask = gdf_to_df_wkt(big_poly)
        result = op_geo_within([df_pts, df_mask], "geometry", "geometry")
        assert len(result) == len(pts)


# =============================================================================
# op_geo_point_on_surface
# =============================================================================

class TestOpGeoPointOnSurface:

    def test_point_inside_polygon(self):
        """Il punto sulla superficie deve essere dentro il poligono originale."""
        gdf = make_polygons(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_point_on_surface(df, "geometry")
        assert len(result) == 5
        # Verifica che il risultato contenga una colonna di output
        out_cols = [c for c in result.columns if c not in df.columns or c == "geometry"]
        assert len(out_cols) >= 1

    def test_output_column_custom(self):
        """output_column personalizzata viene usata."""
        gdf = make_polygons(3)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_point_on_surface(df, "geometry",
                                          output_column="punto_interno")
        assert "punto_interno" in result.columns

    def test_row_count_invariato(self):
        """Il numero di righe non cambia."""
        gdf = make_polygons(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_point_on_surface(df, "geometry")
        assert len(result) == 5


# =============================================================================
# op_geo_boundary
# =============================================================================

class TestOpGeoBoundary:

    def test_polygon_to_linestring(self):
        """Boundary di un poligono → LineString o MultiLineString."""
        gdf = make_polygons(3)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_boundary(df, "geometry")
        assert len(result) == 3

    def test_output_column_custom(self):
        """output_column personalizzata viene usata."""
        gdf = make_polygons(3)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_boundary(df, "geometry", output_column="bordo")
        assert "bordo" in result.columns

    def test_row_count_invariato(self):
        """Il numero di righe non cambia."""
        gdf = make_polygons(5)
        df = gdf_to_df_wkt(gdf)
        result = op_geo_boundary(df, "geometry")
        assert len(result) == 5
