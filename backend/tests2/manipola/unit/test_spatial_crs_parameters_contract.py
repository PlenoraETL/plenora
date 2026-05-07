"""
Contract test — parametri output_crs/metric_crs opt-in su operator spatial.

Il modulo manipola/transforms/spatial.py ha 7 operator che riproiettano
"""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon, LineString


pytestmark = [pytest.mark.unit, pytest.mark.manipola, pytest.mark.contract]


# Fixtures


@pytest.fixture
def gdf_point_bologna():
    """Punto singolo WGS84 a Bologna."""
    return gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[Point(11.34, 44.49)],
        crs="EPSG:4326",
    )


@pytest.fixture
def gdf_polygon_bologna():
    """Poligono quadrato ~1km intorno a Bologna in WGS84."""
    lon, lat = 11.34, 44.49
    d = 0.01
    poly = Polygon([(lon - d, lat - d), (lon + d, lat - d),
                    (lon + d, lat + d), (lon - d, lat + d)])
    return gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")


@pytest.fixture
def gdf_line_bologna():
    """Segmento WGS84 a Bologna."""
    return gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[LineString([(11.34, 44.49), (11.35, 44.49)])],
        crs="EPSG:4326",
    )


# 1. BUFFER — geom output


def test_buffer_default_preserves_input_crs(gdf_point_bologna):
    """Backward compat: omettere i nuovi parametri -> output in CRS input."""
    from app.core.manipola.transforms.spatial import op_geo_buffer
    result = op_geo_buffer(gdf_point_bologna, "geometry", 100, "buf")
    assert str(result.crs) == "EPSG:4326"
    buf_geom = result["buf"].iloc[0]
    # Il poligono e' in WGS84 quindi le coordinate sono gradi
    assert abs(buf_geom.centroid.x - 11.34) < 0.01


def test_buffer_output_crs_metric_skips_roundtrip(gdf_point_bologna):
    """output_crs='metric' -> buffer restituito in UTM, non in WGS84."""
    from app.core.manipola.transforms.spatial import op_geo_buffer
    result = op_geo_buffer(
        gdf_point_bologna, "geometry", 100, "buf", output_crs="metric",
    )
    buf_geom = result["buf"].iloc[0]
    # Centroide in UTM32N ~675000E, 4929000N (approssimativo)
    assert buf_geom.centroid.x > 100000  # non piu' gradi
    assert buf_geom.centroid.y > 1000000


def test_buffer_output_crs_explicit_epsg(gdf_point_bologna):
    """output_crs='EPSG:3857' -> buffer in Web Mercator."""
    from app.core.manipola.transforms.spatial import op_geo_buffer
    result = op_geo_buffer(
        gdf_point_bologna, "geometry", 100, "buf", output_crs="EPSG:3857",
    )
    buf_geom = result["buf"].iloc[0]
    # Web Mercator: X ~1262000 per lon=11.34
    assert 1200000 < buf_geom.centroid.x < 1300000


def test_buffer_metric_crs_overrides_utm(gdf_point_bologna):
    """metric_crs='EPSG:3857' -> buffer misurato in Web Mercator.
    In Web Mercator a lat 44.49 la scala e' deformata: 100 unita' != 100 metri.
    Il buffer risultante, se lasciato in 3857, avra' raggio 100 (unita' 3857).
    """
    from app.core.manipola.transforms.spatial import op_geo_buffer
    result = op_geo_buffer(
        gdf_point_bologna, "geometry", 100, "buf",
        metric_crs="EPSG:3857", output_crs="metric",
    )
    buf_geom = result["buf"].iloc[0]
    center = buf_geom.centroid
    # Raggio misurato in 3857
    bounds = buf_geom.bounds
    radius = (bounds[2] - bounds[0]) / 2
    assert abs(radius - 100.0) < 0.5


def test_buffer_100m_precision_regression(gdf_point_bologna):
    """Contract calibrato: buffer 100m in UTM deve essere 100m ±0.01m.
    Non e' un contract generico: fissa CRS input, zona UTM, precisione."""
    from app.core.manipola.transforms.spatial import op_geo_buffer
    result = op_geo_buffer(
        gdf_point_bologna, "geometry", 100, "buf", output_crs="metric",
    )
    buf_geom = result["buf"].iloc[0]
    # Misura il raggio dal centroide UTM del punto originale
    utm_gdf = gdf_point_bologna.to_crs("EPSG:32632")
    center_utm = utm_gdf.geometry.iloc[0]
    # Un punto del bordo del buffer
    boundary_pt = list(buf_geom.exterior.coords)[0]
    dist = center_utm.distance(Point(boundary_pt[0], boundary_pt[1]))
    assert abs(dist - 100.0) < 0.01, f"raggio misurato: {dist} m"


# 2. CENTROID — geom output


def test_centroid_default_preserves_input_crs(gdf_polygon_bologna):
    from app.core.manipola.transforms.spatial import op_geo_centroid
    result = op_geo_centroid(gdf_polygon_bologna, "geometry", "c")
    centroid = result["c"].iloc[0]
    assert 11.33 < centroid.x < 11.35


def test_centroid_output_crs_metric(gdf_polygon_bologna):
    from app.core.manipola.transforms.spatial import op_geo_centroid
    result = op_geo_centroid(
        gdf_polygon_bologna, "geometry", "c", output_crs="metric",
    )
    centroid = result["c"].iloc[0]
    assert centroid.x > 100000  # UTM, non piu' gradi


def test_centroid_output_crs_explicit_epsg(gdf_polygon_bologna):
    from app.core.manipola.transforms.spatial import op_geo_centroid
    result = op_geo_centroid(
        gdf_polygon_bologna, "geometry", "c", output_crs="EPSG:3857",
    )
    centroid = result["c"].iloc[0]
    assert 1200000 < centroid.x < 1300000


# 3. AREA — numeric output


def test_area_default_in_square_meters_via_auto_utm(gdf_polygon_bologna):
    """Backward compat: area in m^2 via auto-UTM."""
    from app.core.manipola.transforms.spatial import op_geo_area
    result = op_geo_area(gdf_polygon_bologna, "geometry", "a")
    area = result["a"].iloc[0]
    # Quadrato di 0.02° a lat 44.49 -> ~1587m x 2222m ~ 3.5M m^2
    assert 3_000_000 < area < 4_000_000


def test_area_metric_crs_changes_measurement(gdf_polygon_bologna):
    """metric_crs custom produce valori numerici diversi dal default UTM."""
    from app.core.manipola.transforms.spatial import op_geo_area
    utm_result = op_geo_area(gdf_polygon_bologna, "geometry", "a")
    laea_result = op_geo_area(
        gdf_polygon_bologna, "geometry", "a", metric_crs="EPSG:3035",
    )
    # Entrambi in m^2 ma CRS diversi -> valori simili ma non identici
    utm_area = utm_result["a"].iloc[0]
    laea_area = laea_result["a"].iloc[0]
    # Differenza sub-percentuale attesa, ma diversi
    assert abs(utm_area - laea_area) > 0
    assert abs(utm_area - laea_area) / utm_area < 0.05


# 4. LENGTH — numeric output


def test_length_default_via_auto_utm(gdf_line_bologna):
    from app.core.manipola.transforms.spatial import op_geo_length
    result = op_geo_length(gdf_line_bologna, "geometry", "l")
    length = result["l"].iloc[0]
    # Segmento di 0.01° lon a lat 44.49 -> ~793m
    assert 700 < length < 900


def test_length_metric_crs_overrides_utm(gdf_line_bologna):
    from app.core.manipola.transforms.spatial import op_geo_length
    utm_result = op_geo_length(gdf_line_bologna, "geometry", "l")
    laea_result = op_geo_length(
        gdf_line_bologna, "geometry", "l", metric_crs="EPSG:3035",
    )
    assert utm_result["l"].iloc[0] != laea_result["l"].iloc[0]


# 5. PERIMETER — numeric output


def test_perimeter_default_via_auto_utm(gdf_polygon_bologna):
    from app.core.manipola.transforms.spatial import op_geo_perimeter
    result = op_geo_perimeter(gdf_polygon_bologna, "geometry", "p")
    perim = result["p"].iloc[0]
    # Quadrato ~1587m x 2222m -> perimetro ~7600m
    assert 7000 < perim < 8500


def test_perimeter_metric_crs_overrides(gdf_polygon_bologna):
    from app.core.manipola.transforms.spatial import op_geo_perimeter
    utm_result = op_geo_perimeter(gdf_polygon_bologna, "geometry", "p")
    laea_result = op_geo_perimeter(
        gdf_polygon_bologna, "geometry", "p", metric_crs="EPSG:3035",
    )
    assert utm_result["p"].iloc[0] != laea_result["p"].iloc[0]


# 6. DISTANCE — numeric output (2-input)


def test_distance_default_meters_via_auto_utm():
    from app.core.manipola.transforms.spatial import op_geo_distance
    left = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(11.34, 44.49)], crs="EPSG:4326",
    )
    right = gpd.GeoDataFrame(
        {"id": [2]}, geometry=[Point(11.35, 44.49)], crs="EPSG:4326",
    )
    result = op_geo_distance([left, right], "geometry", "geometry", "d")
    dist = result["d"].iloc[0]
    assert 700 < dist < 900


def test_distance_metric_crs_overrides_utm():
    from app.core.manipola.transforms.spatial import op_geo_distance
    left = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(11.34, 44.49)], crs="EPSG:4326",
    )
    right = gpd.GeoDataFrame(
        {"id": [2]}, geometry=[Point(11.35, 44.49)], crs="EPSG:4326",
    )
    utm_result = op_geo_distance([left, right], "geometry", "geometry", "d")
    laea_result = op_geo_distance(
        [left, right], "geometry", "geometry", "d", metric_crs="EPSG:3035",
    )
    assert utm_result["d"].iloc[0] != laea_result["d"].iloc[0]


# 7. NEAREST — geom + numeric output (2-input)


def test_nearest_default_preserves_input_crs():
    from app.core.manipola.transforms.spatial import op_geo_nearest
    left = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(11.34, 44.49)], crs="EPSG:4326",
    )
    right = gpd.GeoDataFrame(
        {"id": [2]}, geometry=[Point(11.35, 44.49)], crs="EPSG:4326",
    )
    result = op_geo_nearest([left, right], "geometry", "geometry", None, "d")
    assert str(result.crs) == "EPSG:4326"


def test_nearest_output_crs_metric():
    from app.core.manipola.transforms.spatial import op_geo_nearest
    left = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(11.34, 44.49)], crs="EPSG:4326",
    )
    right = gpd.GeoDataFrame(
        {"id": [2]}, geometry=[Point(11.35, 44.49)], crs="EPSG:4326",
    )
    result = op_geo_nearest(
        [left, right], "geometry", "geometry", None, "d", output_crs="metric",
    )
    assert str(result.crs).startswith("EPSG:326") or str(result.crs).startswith("EPSG:327")


def test_nearest_metric_crs_overrides():
    from app.core.manipola.transforms.spatial import op_geo_nearest
    left = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(11.34, 44.49)], crs="EPSG:4326",
    )
    right = gpd.GeoDataFrame(
        {"id": [2]}, geometry=[Point(11.35, 44.49)], crs="EPSG:4326",
    )
    utm_result = op_geo_nearest([left, right], "geometry", "geometry", None, "d")
    laea_result = op_geo_nearest(
        [left, right], "geometry", "geometry", None, "d", metric_crs="EPSG:3035",
    )
    assert utm_result["d"].iloc[0] != laea_result["d"].iloc[0]
