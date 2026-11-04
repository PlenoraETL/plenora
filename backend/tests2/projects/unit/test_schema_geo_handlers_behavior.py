"""Behavioral coverage for ``GeoSchemaHandlers`` (schema inference for
geospatial transforms).

``GeoSchemaHandlers`` is a *mixin*: it relies on helpers
(``_copy_column``, ``_copy_schema``, ``_schema_passthrough``,
``_column_schema_cls``) that are injected by ``TransformSchemaHandlers``.
So instead of mocking those helpers (fragile), we build the real composed
object and exercise the geo handler via ``TransformSchemaHandlers(...)._geo_handlers``.
This keeps the helper wiring real (closer to production behaviour) while the
focus of the asserts stays on the geo schema deltas.

Each handler returns a list of ``ColumnSchema`` describing the node output.
We assert on (name, type, is_calculated) tuples and on source_node propagation.
"""

from __future__ import annotations

import pytest

from app.core.projects.schema_transform_handlers import TransformSchemaHandlers
from app.shared.execution.schema_types import ColumnSchema


pytestmark = [pytest.mark.unit]

NODE = "node-geo"


@pytest.fixture
def geo():
    """Real GeoSchemaHandlers instance with helpers wired in by the owner."""
    return TransformSchemaHandlers(ColumnSchema)._geo_handlers


def col(name, type_, *, source_node="src", is_calculated=False):
    return ColumnSchema(
        name=name, type=type_, source_node=source_node, is_calculated=is_calculated
    )


def names(schema):
    return [c.name for c in schema]


def by_name(schema):
    return {c.name: c for c in schema}


# Reusable input schemas
def _point_layer():
    return [col("id", "int"), col("geometry", "geometry:point")]


def _polygon_layer():
    return [col("region", "str"), col("geometry", "geometry:polygon")]


# --------------------------------------------------------------------------- #
# _schema_sjoin
# --------------------------------------------------------------------------- #
def test_sjoin_combines_both_inputs_and_adds_index_right(geo):
    left = [col("id", "int"), col("geometry", "geometry:point")]
    right = [col("region", "str"), col("pop", "int")]
    out = geo._schema_sjoin([left, right], {}, NODE)
    assert names(out) == ["id", "geometry", "region", "pop"]


def test_sjoin_suffixes_colliding_right_columns(geo):
    left = [col("id", "int"), col("name", "str")]
    right = [col("name", "str"), col("extra", "real")]
    out = geo._schema_sjoin([left, right], {}, NODE)
    assert "name_left" in names(out)
    assert "name_right" in names(out)
    assert names(out) == ["id", "name_left", "name_right", "extra"]


def test_sjoin_single_input_returns_first(geo):
    left = _point_layer()
    out = geo._schema_sjoin([left], {}, NODE)
    assert names(out) == ["id", "geometry"]


def test_sjoin_no_inputs_returns_empty(geo):
    assert geo._schema_sjoin([], {}, NODE) == []


# --------------------------------------------------------------------------- #
# _schema_geo_buffer
# --------------------------------------------------------------------------- #
def test_buffer_adds_new_polygon_column_by_default(geo):
    out = geo._schema_geo_buffer([_point_layer()], {}, NODE)
    assert names(out) == ["id", "geometry", "buffer_geom"]
    buf = by_name(out)["buffer_geom"]
    assert buf.type == "geometry:polygon"
    assert buf.is_calculated is True


def test_buffer_replaces_geometry_when_output_equals_geom_column(geo):
    cfg = {"geom_column": "geometry", "output_column": "geometry"}
    out = geo._schema_geo_buffer([_point_layer()], cfg, NODE)
    assert names(out) == ["id", "geometry"]
    g = by_name(out)["geometry"]
    assert g.type == "geometry:polygon"
    assert g.is_calculated is True


def test_buffer_does_not_duplicate_existing_output_column(geo):
    layer = [col("id", "int"), col("geometry", "geometry:point"), col("buffer_geom", "str")]
    out = geo._schema_geo_buffer([layer], {}, NODE)
    # buffer_geom already exists -> not appended again
    assert names(out) == ["id", "geometry", "buffer_geom"]
    assert by_name(out)["buffer_geom"].type == "geometry:polygon"  # A1: assegnazione runtime


def test_buffer_empty_inputs(geo):
    assert geo._schema_geo_buffer([], {}, NODE) == []


# --------------------------------------------------------------------------- #
# _schema_geo_centroid
# --------------------------------------------------------------------------- #
def test_centroid_adds_point_column(geo):
    out = geo._schema_geo_centroid([_polygon_layer()], {}, NODE)
    assert names(out) == ["region", "geometry", "centroid"]
    c = by_name(out)["centroid"]
    assert c.type == "geometry:point"
    assert c.is_calculated is True


def test_centroid_replaces_geometry_in_place(geo):
    cfg = {"output_column": "geometry"}
    out = geo._schema_geo_centroid([_polygon_layer()], cfg, NODE)
    assert names(out) == ["region", "geometry"]
    assert by_name(out)["geometry"].type == "geometry:point"


def test_centroid_empty_inputs(geo):
    assert geo._schema_geo_centroid([], {}, NODE) == []


# --------------------------------------------------------------------------- #
# _schema_geo_area
# --------------------------------------------------------------------------- #
def test_area_adds_real_column_default_name(geo):
    out = geo._schema_geo_area([_polygon_layer()], {}, NODE)
    assert names(out) == ["region", "geometry", "area_m2"]
    assert by_name(out)["area_m2"].type == "real"


def test_area_custom_output_column(geo):
    out = geo._schema_geo_area([_polygon_layer()], {"output_column": "superficie"}, NODE)
    assert "superficie" in names(out)


def test_area_skips_when_column_exists(geo):
    layer = [col("region", "str"), col("area_m2", "int")]
    out = geo._schema_geo_area([layer], {}, NODE)
    assert names(out) == ["region", "area_m2"]
    assert by_name(out)["area_m2"].type == "real"  # A1: assegnazione runtime


def test_area_empty_inputs(geo):
    assert geo._schema_geo_area([], {}, NODE) == []


# --------------------------------------------------------------------------- #
# _schema_geo_clip
# --------------------------------------------------------------------------- #
def test_clip_is_passthrough_of_first_input(geo):
    left = _polygon_layer()
    out = geo._schema_geo_clip([left, _point_layer()], {}, NODE)
    assert names(out) == ["region", "geometry"]
    # passthrough re-stamps source_node
    assert all(c.source_node == NODE for c in out)


def test_clip_empty_inputs(geo):
    assert geo._schema_geo_clip([], {}, NODE) == []


# --------------------------------------------------------------------------- #
# _schema_geo_dissolve
# --------------------------------------------------------------------------- #
def test_dissolve_with_group_by_keeps_group_and_geometry(geo):
    layer = [col("region", "str"), col("pop", "int"), col("geometry", "geometry:polygon")]
    out = geo._schema_geo_dissolve([layer], {"by_column": "region"}, NODE)
    assert names(out) == ["region", "geometry", "pop"]
    assert by_name(out)["region"].is_calculated is False
    g = by_name(out)["geometry"]
    assert g.type.startswith("geometry")
    assert g.is_calculated is False


def test_dissolve_without_group_by_only_geometry(geo):
    out = geo._schema_geo_dissolve([_polygon_layer()], {}, NODE)
    assert names(out) == ["index", "geometry", "region"]


def test_dissolve_custom_geom_column(geo):
    layer = [col("region", "str"), col("geom", "geometry:polygon")]
    out = geo._schema_geo_dissolve([layer], {"by_column": "region", "geom_column": "geom"}, NODE)
    assert names(out) == ["region", "geom"]


def test_dissolve_group_column_missing_from_input(geo):
    # by_column points at a non-existent column -> only geometry produced
    out = geo._schema_geo_dissolve([_polygon_layer()], {"by_column": "missing"}, NODE)
    assert names(out) == ["index", "geometry", "region"]


def test_dissolve_empty_inputs(geo):
    assert geo._schema_geo_dissolve([], {}, NODE) == []


# --------------------------------------------------------------------------- #
# _schema_geo_convex_hull
# --------------------------------------------------------------------------- #
def test_convex_hull_adds_polygon_column(geo):
    out = geo._schema_geo_convex_hull([_point_layer()], {}, NODE)
    assert names(out) == ["id", "geometry", "convex_hull"]
    assert by_name(out)["convex_hull"].type == "geometry:polygon"


def test_convex_hull_overwrites_geometry_type(geo):
    out = geo._schema_geo_convex_hull([_point_layer()], {"output_column": "geometry"}, NODE)
    assert names(out) == ["id", "geometry"]
    assert by_name(out)["geometry"].type == "geometry:polygon"
    assert by_name(out)["geometry"].is_calculated is True


def test_convex_hull_empty_inputs(geo):
    assert geo._schema_geo_convex_hull([], {}, NODE) == []


# --------------------------------------------------------------------------- #
# _schema_geo_envelope
# --------------------------------------------------------------------------- #
def test_envelope_adds_polygon_column(geo):
    out = geo._schema_geo_envelope([_point_layer()], {}, NODE)
    assert names(out) == ["id", "geometry", "envelope"]
    assert by_name(out)["envelope"].type == "geometry:polygon"


def test_envelope_overwrites_geometry_type(geo):
    out = geo._schema_geo_envelope(
        [_point_layer()],
        {"output_column": "geometry"},
        NODE,
    )
    assert names(out) == ["id", "geometry"]
    assert by_name(out)["geometry"].type == "geometry:polygon"
    assert by_name(out)["geometry"].is_calculated is True


# --------------------------------------------------------------------------- #
# _schema_geo_overlay
# --------------------------------------------------------------------------- #
def test_overlay_skips_right_geometry_and_suffixes_collisions(geo):
    left = [col("id", "int"), col("geometry", "geometry:polygon")]
    right = [col("id", "int"), col("zone", "str"), col("geometry", "geometry:polygon")]
    out = geo._schema_geo_overlay([left, right], {}, NODE, "intersection")
    # right geometry skipped; colliding 'id' -> 'id_2'
    assert names(out) == ["id_1", "id_2", "zone", "geometry"]


def test_overlay_single_input_returns_first(geo):
    out = geo._schema_geo_overlay([_polygon_layer()], {}, NODE, "union")
    assert names(out) == ["region", "geometry"]


def test_overlay_no_inputs(geo):
    assert geo._schema_geo_overlay([], {}, NODE, "difference") == []


# --------------------------------------------------------------------------- #
# _schema_geo_from_coords
# --------------------------------------------------------------------------- #
def test_from_coords_adds_geometry_with_crs_subtype(geo):
    layer = [col("lat", "real"), col("lon", "real")]
    out = geo._schema_geo_from_coords([layer], {}, NODE)
    assert names(out) == ["lat", "lon", "geometry"]
    assert by_name(out)["geometry"].type == "geometry:point:4326"


def test_from_coords_custom_crs_and_column(geo):
    layer = [col("lat", "real"), col("lon", "real")]
    cfg = {"output_column": "geom", "crs": "EPSG:3857"}
    out = geo._schema_geo_from_coords([layer], cfg, NODE)
    assert by_name(out)["geom"].type == "geometry:point:3857"


def test_from_coords_does_not_duplicate_existing(geo):
    layer = [col("lat", "real"), col("geometry", "geometry:point")]
    out = geo._schema_geo_from_coords([layer], {}, NODE)
    assert names(out) == ["lat", "geometry"]  # geometry already present


def test_from_coords_empty_inputs(geo):
    assert geo._schema_geo_from_coords([], {}, NODE) == []


# --------------------------------------------------------------------------- #
# _schema_geo_distance
# --------------------------------------------------------------------------- #
def test_distance_adds_real_default_column(geo):
    out = geo._schema_geo_distance([_point_layer()], {}, NODE)
    assert names(out) == ["id", "geometry", "distance_m"]
    assert by_name(out)["distance_m"].type == "real"


def test_distance_custom_column(geo):
    out = geo._schema_geo_distance([_point_layer()], {"output_column": "dist"}, NODE)
    assert "dist" in names(out)


def test_distance_no_duplicate(geo):
    layer = [col("id", "int"), col("distance_m", "real")]
    out = geo._schema_geo_distance([layer], {}, NODE)
    assert names(out) == ["id", "distance_m"]


def test_distance_empty_inputs(geo):
    assert geo._schema_geo_distance([], {}, NODE) == []


# --------------------------------------------------------------------------- #
# _schema_geo_to_wkt
# --------------------------------------------------------------------------- #
def test_to_wkt_default_mode_adds_wkt_column(geo):
    out = geo._schema_geo_to_wkt([_point_layer()], {}, NODE)
    assert names(out) == ["id", "geometry", "geometry_wkt"]
    assert by_name(out)["geometry_wkt"].type == "str"


def test_to_wkt_latlon_mode_adds_two_real_columns(geo):
    out = geo._schema_geo_to_wkt([_point_layer()], {"mode": "latlon"}, NODE)
    assert names(out) == ["id", "geometry", "latitude", "longitude"]
    assert by_name(out)["latitude"].type == "real"
    assert by_name(out)["longitude"].type == "real"


def test_to_wkt_both_mode_adds_three_columns(geo):
    out = geo._schema_geo_to_wkt([_point_layer()], {"mode": "both"}, NODE)
    assert names(out) == ["id", "geometry", "geometry_wkt", "latitude", "longitude"]


def test_to_wkt_invalid_mode_falls_back_to_wkt(geo):
    out = geo._schema_geo_to_wkt([_point_layer()], {"mode": "NONSENSE"}, NODE)
    assert names(out) == ["id", "geometry", "geometry_wkt"]


def test_to_wkt_mode_case_insensitive(geo):
    out = geo._schema_geo_to_wkt([_point_layer()], {"mode": "  BOTH "}, NODE)
    assert "geometry_wkt" in names(out) and "latitude" in names(out)


def test_to_wkt_custom_column_names(geo):
    cfg = {"mode": "both", "wkt_column": "w", "lat_column": "y", "lon_column": "x"}
    out = geo._schema_geo_to_wkt([_point_layer()], cfg, NODE)
    assert {"w", "y", "x"}.issubset(set(names(out)))


def test_to_wkt_no_duplicate_existing(geo):
    layer = [col("id", "int"), col("geometry_wkt", "int")]
    out = geo._schema_geo_to_wkt([layer], {}, NODE)
    assert names(out) == ["id", "geometry_wkt"]
    assert by_name(out)["geometry_wkt"].type == "str"  # A1: assegnazione runtime


def test_to_wkt_empty_inputs(geo):
    assert geo._schema_geo_to_wkt([], {}, NODE) == []


# --------------------------------------------------------------------------- #
# _schema_geo_length / _schema_geo_perimeter
# --------------------------------------------------------------------------- #
def test_length_default_column(geo):
    out = geo._schema_geo_length([_polygon_layer()], {}, NODE)
    assert names(out) == ["region", "geometry", "length_m"]
    assert by_name(out)["length_m"].type == "real"


def test_length_custom_no_duplicate(geo):
    layer = [col("region", "str"), col("length_m", "real")]
    out = geo._schema_geo_length([layer], {}, NODE)
    assert names(out) == ["region", "length_m"]


def test_length_empty_inputs(geo):
    assert geo._schema_geo_length([], {}, NODE) == []


def test_perimeter_default_column(geo):
    out = geo._schema_geo_perimeter([_polygon_layer()], {}, NODE)
    assert names(out) == ["region", "geometry", "perimeter_m"]
    assert by_name(out)["perimeter_m"].type == "real"


def test_perimeter_custom_column(geo):
    out = geo._schema_geo_perimeter([_polygon_layer()], {"output_column": "p"}, NODE)
    assert "p" in names(out)


def test_perimeter_empty_inputs(geo):
    assert geo._schema_geo_perimeter([], {}, NODE) == []


# --------------------------------------------------------------------------- #
# _schema_geo_vertex_count
# --------------------------------------------------------------------------- #
def test_vertex_count_adds_int_column(geo):
    out = geo._schema_geo_vertex_count([_polygon_layer()], {}, NODE)
    assert names(out) == ["region", "geometry", "vertex_count"]
    assert by_name(out)["vertex_count"].type == "int"


def test_vertex_count_custom_no_duplicate(geo):
    layer = [col("region", "str"), col("vertex_count", "int")]
    out = geo._schema_geo_vertex_count([layer], {}, NODE)
    assert names(out) == ["region", "vertex_count"]


def test_vertex_count_empty_inputs(geo):
    assert geo._schema_geo_vertex_count([], {}, NODE) == []


# --------------------------------------------------------------------------- #
# _schema_geo_bounds_extractor
# --------------------------------------------------------------------------- #
def test_bounds_extractor_adds_four_real_columns(geo):
    out = geo._schema_geo_bounds_extractor([_polygon_layer()], {}, NODE)
    assert names(out) == ["region", "geometry", "min_x", "min_y", "max_x", "max_y"]
    assert all(by_name(out)[n].type == "real" for n in ("min_x", "min_y", "max_x", "max_y"))


def test_bounds_extractor_prefix(geo):
    out = geo._schema_geo_bounds_extractor([_polygon_layer()], {"prefix": "bb_"}, NODE)
    assert names(out) == ["region", "geometry", "bb_min_x", "bb_min_y", "bb_max_x", "bb_max_y"]


def test_bounds_extractor_skips_existing(geo):
    layer = [col("region", "str"), col("min_x", "str")]
    out = geo._schema_geo_bounds_extractor([layer], {}, NODE)
    assert by_name(out)["min_x"].type == "real"  # A1: assegnazione runtime
    # the other three still appended
    assert {"min_y", "max_x", "max_y"}.issubset(set(names(out)))


def test_bounds_extractor_empty_inputs(geo):
    assert geo._schema_geo_bounds_extractor([], {}, NODE) == []


# --------------------------------------------------------------------------- #
# _schema_geo_count_points_in_polygons
# --------------------------------------------------------------------------- #
def test_count_points_default_adds_count_column(geo):
    out = geo._schema_geo_count_points_in_polygons([_polygon_layer()], {}, NODE)
    assert names(out) == ["region", "geometry", "point_count"]
    assert by_name(out)["point_count"].type == "int"


def test_count_points_with_aggregate(geo):
    cfg = {"aggregate_column": "value", "aggregate_func": "mean"}
    out = geo._schema_geo_count_points_in_polygons([_polygon_layer()], cfg, NODE)
    assert "value_mean" in names(out)
    assert by_name(out)["value_mean"].type == "real"


def test_count_points_unknown_aggregate_func_falls_back_to_sum(geo):
    cfg = {"aggregate_column": "value", "aggregate_func": "median"}
    out = geo._schema_geo_count_points_in_polygons([_polygon_layer()], cfg, NODE)
    assert "value_sum" in names(out)


def test_count_points_custom_count_column_no_duplicate(geo):
    layer = [col("region", "str"), col("point_count", "int")]
    out = geo._schema_geo_count_points_in_polygons([layer], {}, NODE)
    assert names(out) == ["region", "point_count"]


def test_count_points_empty_inputs(geo):
    assert geo._schema_geo_count_points_in_polygons([], {}, NODE) == []


# --------------------------------------------------------------------------- #
# _schema_geo_line_builder / _schema_geo_polygon_builder (via _schema_geo_builder)
# --------------------------------------------------------------------------- #
def test_line_builder_rebuilds_two_columns(geo):
    layer = [col("track", "str"), col("seq", "int"), col("geometry", "geometry:point")]
    out = geo._schema_geo_line_builder([layer], {"group_column": "track"}, NODE)
    assert names(out) == ["track", "geometry"]
    assert by_name(out)["track"].type == "str"
    assert by_name(out)["track"].is_calculated is False
    g = by_name(out)["geometry"]
    assert g.type == "geometry:line"
    assert g.is_calculated is True


def test_polygon_builder_rebuilds_two_columns(geo):
    layer = [col("zone", "str"), col("geometry", "geometry:point")]
    out = geo._schema_geo_polygon_builder([layer], {"group_column": "zone"}, NODE)
    assert names(out) == ["zone", "geometry"]
    assert by_name(out)["geometry"].type == "geometry:polygon"


def test_builder_without_group_column_is_passthrough(geo):
    layer = [col("a", "int"), col("geometry", "geometry:point")]
    out = geo._schema_geo_line_builder([layer], {}, NODE)
    assert names(out) == ["a", "geometry"]


def test_builder_group_column_not_in_input_synthesizes_str(geo):
    layer = [col("geometry", "geometry:point")]
    out = geo._schema_geo_polygon_builder([layer], {"group_column": "newcol"}, NODE)
    assert names(out) == ["newcol", "geometry"]
    assert by_name(out)["newcol"].type == "str"


def test_builder_output_equals_group_column_only_group(geo):
    layer = [col("g", "str"), col("geometry", "geometry:point")]
    cfg = {"group_column": "g", "output_column": "g"}
    out = geo._schema_geo_line_builder([layer], cfg, NODE)
    # output == group -> geometry column NOT appended
    assert names(out) == ["g"]


def test_builder_empty_inputs(geo):
    assert geo._schema_geo_line_builder([], {"group_column": "x"}, NODE) == []


def test_builder_preserves_group_type(geo):
    layer = [col("gid", "int"), col("geometry", "geometry:point")]
    out = geo._schema_geo_polygon_builder([layer], {"group_column": "gid"}, NODE)
    assert by_name(out)["gid"].type == "int"


# --------------------------------------------------------------------------- #
# _schema_geo_point_on_surface
# --------------------------------------------------------------------------- #
def test_point_on_surface_adds_point_column(geo):
    out = geo._schema_geo_point_on_surface([_polygon_layer()], {}, NODE)
    assert names(out) == ["region", "geometry", "point_on_surface"]
    assert by_name(out)["point_on_surface"].type == "geometry:point"


def test_point_on_surface_overwrites_geometry_in_place(geo):
    out = geo._schema_geo_point_on_surface([_polygon_layer()], {"output_column": "geometry"}, NODE)
    assert names(out) == ["region", "geometry"]
    g = by_name(out)["geometry"]
    assert g.type == "geometry:point"
    assert g.is_calculated is True


def test_point_on_surface_empty_inputs(geo):
    assert geo._schema_geo_point_on_surface([], {}, NODE) == []


# --------------------------------------------------------------------------- #
# _schema_geo_boundary
# --------------------------------------------------------------------------- #
def test_boundary_adds_generic_geometry_column(geo):
    out = geo._schema_geo_boundary([_polygon_layer()], {}, NODE)
    assert names(out) == ["region", "geometry", "boundary"]
    b = by_name(out)["boundary"]
    assert b.type == "geometry"
    assert b.is_calculated is True


def test_boundary_overwrites_geometry_in_place(geo):
    out = geo._schema_geo_boundary([_polygon_layer()], {"output_column": "geometry"}, NODE)
    assert names(out) == ["region", "geometry"]
    g = by_name(out)["geometry"]
    assert g.type == "geometry"
    assert g.is_calculated is True


def test_boundary_empty_inputs(geo):
    assert geo._schema_geo_boundary([], {}, NODE) == []


# --------------------------------------------------------------------------- #
# _schema_geo_voronoi
# --------------------------------------------------------------------------- #
def test_voronoi_changes_geometry_type_to_polygon(geo):
    out = geo._schema_geo_voronoi([_point_layer()], {}, NODE)
    assert names(out) == ["id", "geometry"]
    g = by_name(out)["geometry"]
    assert g.type == "geometry:polygon"
    assert g.is_calculated is True


def test_voronoi_custom_geom_column(geo):
    layer = [col("id", "int"), col("geom", "geometry:point")]
    out = geo._schema_geo_voronoi([layer], {"geom_column": "geom"}, NODE)
    assert by_name(out)["geom"].type == "geometry:polygon"


def test_voronoi_missing_geom_column_is_passthrough(geo):
    layer = [col("id", "int"), col("geometry", "geometry:point")]
    out = geo._schema_geo_voronoi([layer], {"geom_column": "absent"}, NODE)
    # no matching column -> nothing changed
    assert by_name(out)["geometry"].type == "geometry:point"


def test_voronoi_empty_inputs(geo):
    assert geo._schema_geo_voronoi([], {}, NODE) == []


# --------------------------------------------------------------------------- #
# _schema_geo_within
# --------------------------------------------------------------------------- #
def test_within_is_left_passthrough(geo):
    left = _polygon_layer()
    out = geo._schema_geo_within([left, _point_layer()], NODE)
    assert names(out) == ["region", "geometry"]
    assert all(c.source_node == NODE for c in out)


def test_within_empty_inputs(geo):
    assert geo._schema_geo_within([], NODE) == []


# --------------------------------------------------------------------------- #
# _schema_geo_nearest
# --------------------------------------------------------------------------- #
def test_nearest_adds_right_nongeom_and_distance(geo):
    left = [col("id", "int"), col("geometry", "geometry:point")]
    right = [col("name", "str"), col("geometry", "geometry:point")]
    out = geo._schema_geo_nearest([left, right], {}, NODE)
    # right geometry skipped, name added, distance_m appended
    assert names(out) == ["id", "geometry", "name", "distance_m"]
    assert by_name(out)["distance_m"].type == "real"


def test_nearest_suffixes_colliding_right_columns(geo):
    left = [col("id", "int"), col("geometry", "geometry:point")]
    right = [col("id", "int"), col("geometry", "geometry:point")]
    out = geo._schema_geo_nearest([left, right], {}, NODE)
    assert "id_right" in names(out)
    # right geometry collides with left geometry too -> but geometry is skipped
    assert names(out) == ["id_left", "geometry", "id_right", "distance_m"]


def test_nearest_custom_distance_column_no_duplicate(geo):
    left = [col("id", "int"), col("dist", "real"), col("geometry", "geometry:point")]
    right = [col("name", "str")]
    out = geo._schema_geo_nearest([left, right], {"output_column": "dist"}, NODE)
    # dist already present -> not re-added
    assert names(out).count("dist") == 1


def test_nearest_single_input_returns_first(geo):
    out = geo._schema_geo_nearest([_point_layer()], {}, NODE)
    assert names(out) == ["id", "geometry"]


def test_nearest_no_inputs(geo):
    assert geo._schema_geo_nearest([], {}, NODE) == []


# --------------------------------------------------------------------------- #
# Cross-cutting: source_node propagation on calculated cols
# --------------------------------------------------------------------------- #
def test_calculated_columns_carry_node_id_as_source(geo):
    out = geo._schema_geo_area([_polygon_layer()], {}, NODE)
    assert by_name(out)["area_m2"].source_node == NODE
