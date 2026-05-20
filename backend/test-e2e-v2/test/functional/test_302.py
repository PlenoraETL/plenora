"""test_302 - MANIPOLA single-input geospatial transforms execute directly.

Scenario covered:
- every single-input geo transform missing from the first registry matrix;
- geometry construction, measurement, topology, export and reshape-style
  geospatial operations on deterministic GeoDataFrames.
"""

from __future__ import annotations

import pandas as pd

from _e2e import assert_equal, assert_true
from _manipola_runtime_full import (
    assert_columns,
    assert_geom_types,
    invalid_polygon_frame,
    line_frame,
    multipoint_frame,
    overlapping_polygon_frame,
    point_frame,
    polygon_frame,
    run_silent,
    run_transform,
)


def test_single_input_geo_transforms() -> None:
    points = point_frame()
    polygons = polygon_frame()
    lines = line_frame()

    from_coords = run_transform(
        "geo_from_coords",
        [pd.DataFrame([{"id": 1, "lat": 45.0, "lon": 9.0}, {"id": 2, "lat": 46.0, "lon": 10.0}])],
        {"mode": "latlon", "lat_column": "lat", "lon_column": "lon", "output_column": "geom", "crs": "EPSG:4326"},
    )
    assert_geom_types(from_coords, ["Point", "Point"], "geo_from_coords must create point geometry")

    buffered = run_transform("geo_buffer", [points], {"geom_column": "geom", "distance": 100, "output_column": "buf", "metric_crs": "EPSG:3857"})
    assert_columns(buffered, ["buf"], "geo_buffer must add output geometry column")
    assert_true(buffered["buf"].iloc[0].area > 0, "geo_buffer must create positive-area buffers")

    centroid = run_transform("geo_centroid", [polygons], {"geom_column": "geom", "output_column": "centroid", "metric_crs": "EPSG:3857"})
    assert_equal(centroid["centroid"].iloc[0].geom_type, "Point", "geo_centroid must create point centroids")

    area = run_transform("geo_area", [polygons], {"geom_column": "geom", "output_column": "area", "metric_crs": "EPSG:3857"})
    assert_true(area["area"].min() > 0, "geo_area must compute positive polygon areas")

    dissolved = run_transform("geo_dissolve", [polygons], {"geom_column": "geom", "by_column": "zone", "aggfunc": "first"})
    assert_equal(sorted(dissolved["zone"].tolist()), ["A", "B"], "geo_dissolve must keep one row per group")

    simplified = run_transform("geo_simplify", [polygons], {"geom_column": "geom", "tolerance": 0.01, "preserve_topology": True})
    assert_equal(len(simplified), len(polygons), "geo_simplify must preserve row count")

    hull = run_transform("geo_convex_hull", [polygons], {"geom_column": "geom", "output_column": "hull"})
    assert_equal(hull["hull"].iloc[0].geom_type, "Polygon", "geo_convex_hull must create polygon hulls")

    reprojected = run_transform("geo_reproject", [points], {"geom_column": "geom", "source_crs": "EPSG:4326", "target_crs": "EPSG:3857"})
    assert_equal(str(reprojected.crs).upper(), "EPSG:3857", "geo_reproject must set the target CRS")

    envelope = run_transform("geo_envelope", [polygons], {"geom_column": "geom", "output_column": "env"})
    assert_equal(envelope["env"].iloc[0].geom_type, "Polygon", "geo_envelope must create envelope polygons")

    length = run_transform("geo_length", [lines], {"geom_column": "geom", "output_column": "len", "metric_crs": "EPSG:3857"})
    assert_true(length["len"].min() > 0, "geo_length must compute positive line lengths")

    perimeter = run_transform("geo_perimeter", [polygons], {"geom_column": "geom", "output_column": "perim", "metric_crs": "EPSG:3857"})
    assert_true(perimeter["perim"].min() > 0, "geo_perimeter must compute positive perimeters")

    exploded = run_transform("geo_explode", [multipoint_frame()], {"geom_column": "geom"})
    assert_equal(len(exploded), 3, "geo_explode must split MultiPoint into three rows")

    valid = run_transform("geo_make_valid", [invalid_polygon_frame()], {"geom_column": "geom"})
    assert_true(bool(valid.geometry.is_valid.all()), "geo_make_valid must repair invalid geometry")

    point_on_surface = run_transform("geo_point_on_surface", [polygons], {"geom_column": "geom", "output_column": "inside"})
    assert_true(point_on_surface["inside"].iloc[0].within(polygons.geometry.iloc[0]), "geo_point_on_surface must create interior points")

    boundary = run_transform("geo_boundary", [polygons], {"geom_column": "geom", "output_column": "edge"})
    assert_true(boundary["edge"].iloc[0].geom_type in {"LineString", "MultiLineString"}, "geo_boundary must create boundary lines")

    voronoi = run_transform("geo_voronoi", [points.iloc[:3]], {"geom_column": "geom"})
    assert_equal(len(voronoi), 3, "geo_voronoi must keep one generated polygon per point")
    assert_true(all(g.geom_type == "Polygon" for g in voronoi.geometry), "geo_voronoi must output polygons")

    wkt = run_transform("geo_to_wkt", [polygons], {"geom_column": "geom", "mode": "both", "wkt_column": "wkt", "lat_column": "lat", "lon_column": "lon"})
    assert_columns(wkt, ["wkt", "lat", "lon"], "geo_to_wkt must create WKT and centroid coordinate columns")

    clean = run_transform("geo_clean_topology", [overlapping_polygon_frame()], {"geom_column": "geom", "snap_tolerance": 0.001, "remove_overlaps": True, "fill_gaps": True})
    assert_true(bool(clean.geometry.is_valid.all()), "geo_clean_topology must return valid geometries")

    bounds = run_transform("geo_bounds_extractor", [polygons], {"geom_column": "geom", "prefix": "bbox_"})
    assert_columns(bounds, ["bbox_min_x", "bbox_min_y", "bbox_max_x", "bbox_max_y"], "geo_bounds_extractor must create bbox columns")

    vertex_count = run_transform("geo_vertex_count", [polygons], {"geom_column": "geom", "output_column": "vertices"})
    assert_equal(vertex_count["vertices"].tolist(), [5, 5], "geo_vertex_count must count closed polygon ring vertices")

    line_built = run_transform("geo_line_builder", [points.iloc[:3]], {"geom_column": "geom", "group_column": "group", "order_column": "seq"})
    assert_geom_types(line_built, ["LineString"], "geo_line_builder must build grouped lines")

    polygon_built = run_transform("geo_polygon_builder", [points.iloc[:3]], {"geom_column": "geom", "group_column": "group", "order_column": "seq"})
    assert_geom_types(polygon_built, ["Polygon"], "geo_polygon_builder must build grouped polygons")


if __name__ == "__main__":
    run_silent(test_single_input_geo_transforms)
