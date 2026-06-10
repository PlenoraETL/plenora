"""
Behavioral coverage per `app.core.dataframes.geodata`.

Esercita le boundary geospaziali (costruzione GeoDataFrame/GeoSeries, join/clip/
overlay, group/aggregate, introspezione geom-type/bounds), la conversione
``to_geodataframe`` con i suoi rami (WKT/EWKB/CSV-spezzato/errori) e le utility
``detect_geometry_column`` / ``ensure_same_crs`` / ``extract_srid_from_column_type``.
"""

from __future__ import annotations

import pandas as pd
import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

from app.core.dataframes.geodata import (
    is_geodataframe,
    geodataframe_from_geometry,
    geodataframe_from_rows,
    geodataframe_from_geojson_records,
    geodataframe_from_geometry_series,
    geoseries_from_geometry_values,
    empty_geodataframe,
    geodataframe_sjoin,
    geodataframe_sjoin_nearest,
    geodataframe_clip,
    geodataframe_overlay,
    geodataframe_drop_duplicates_reset_index,
    geodataframe_group_size_named,
    geodataframe_group_aggregate_named,
    geoseries_first_value,
    active_geometry_value_at,
    set_active_geometry_value_at,
    geodataframe_majority_geometry_type,
    geodataframe_total_bounds,
    to_geodataframe,
    detect_geometry_column,
    ensure_same_crs,
    extract_srid_from_column_type,
    parse_geometry,
)


pytestmark = [pytest.mark.unit]


# Helpers / fixtures

def _square(x: float, y: float, s: float = 1.0) -> Polygon:
    return Polygon([(x, y), (x + s, y), (x + s, y + s), (x, y + s)])


@pytest.fixture
def points_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"name": ["a", "b", "c"]},
        geometry=[Point(0, 0), Point(1, 1), Point(5, 5)],
        crs="EPSG:4326",
    )


@pytest.fixture
def squares_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"zone": ["z0", "z1"]},
        geometry=[_square(-1, -1, 3), _square(4, 4, 3)],
        crs="EPSG:4326",
    )


# is_geodataframe

def test_is_geodataframe_true_for_gdf(points_gdf):
    assert is_geodataframe(points_gdf) is True


def test_is_geodataframe_false_for_plain_df():
    assert is_geodataframe(pd.DataFrame({"a": [1]})) is False


def test_is_geodataframe_false_for_non_frame():
    assert is_geodataframe("not a frame") is False
    assert is_geodataframe(None) is False


# Constructors

def test_geodataframe_from_geometry_sets_active_and_crs():
    df = pd.DataFrame({"v": [1, 2], "g": [Point(0, 0), Point(1, 1)]})
    gdf = geodataframe_from_geometry(df, geometry="g", crs="EPSG:3857")
    assert is_geodataframe(gdf)
    assert gdf.geometry.name == "g"
    assert gdf.crs.to_epsg() == 3857


def test_geodataframe_from_rows():
    rows = [
        {"id": 1, "geometry": Point(0, 0)},
        {"id": 2, "geometry": Point(2, 2)},
    ]
    gdf = geodataframe_from_rows(rows, geometry="geometry", crs="EPSG:4326")
    assert is_geodataframe(gdf)
    assert list(gdf["id"]) == [1, 2]
    assert gdf.crs.to_epsg() == 4326


def test_geodataframe_from_geojson_records_happy_path():
    records = [{"name": "p1"}, {"name": "p2"}]
    geometries = [
        {"type": "Point", "coordinates": [0, 0]},
        {"type": "Point", "coordinates": [1, 1]},
    ]
    gdf = geodataframe_from_geojson_records(records, geometries)
    assert is_geodataframe(gdf)
    assert list(gdf["name"]) == ["p1", "p2"]
    assert gdf.geometry.iloc[1].equals(Point(1, 1))
    assert gdf.crs.to_epsg() == 4326


def test_geodataframe_from_geojson_records_none_geometry_kept_as_null():
    records = [{"name": "p1"}, {"name": "p2"}]
    geometries = [{"type": "Point", "coordinates": [0, 0]}, None]
    gdf = geodataframe_from_geojson_records(records, geometries)
    assert gdf.geometry.iloc[0] is not None
    assert gdf.geometry.isna().iloc[1]


def test_geodataframe_from_geojson_records_drop_z_flattens_3d():
    records = [{"name": "p1"}]
    geometries = [{"type": "Point", "coordinates": [0, 0, 9]}]
    gdf = geodataframe_from_geojson_records(records, geometries, drop_z=True)
    assert gdf.geometry.iloc[0].has_z is False


def test_geodataframe_from_geojson_records_keeps_z_without_drop():
    records = [{"name": "p1"}]
    geometries = [{"type": "Point", "coordinates": [0, 0, 9]}]
    gdf = geodataframe_from_geojson_records(records, geometries, drop_z=False)
    assert gdf.geometry.iloc[0].has_z is True


def test_geodataframe_from_geojson_records_empty_records_geometry_only():
    gdf = geodataframe_from_geojson_records([], [None])
    assert is_geodataframe(gdf)
    assert "geometry" in gdf.columns
    assert gdf.geometry.isna().iloc[0]


def test_geodataframe_from_geometry_series():
    gs = gpd.GeoSeries([Point(0, 0), Point(1, 1)], crs="EPSG:4326")
    gdf = geodataframe_from_geometry_series(gs, crs="EPSG:4326")
    assert is_geodataframe(gdf)
    assert len(gdf) == 2


def test_geoseries_from_geometry_values_with_index():
    gs = geoseries_from_geometry_values(
        [Point(0, 0), Point(1, 1)], crs="EPSG:4326", index=[10, 20]
    )
    assert isinstance(gs, gpd.GeoSeries)
    assert list(gs.index) == [10, 20]


def test_empty_geodataframe_has_columns_and_no_rows():
    gdf = empty_geodataframe(["a", "b"])
    assert is_geodataframe(gdf)
    assert list(gdf.columns) == ["a", "b"]
    assert len(gdf) == 0


# Spatial operations

def test_geodataframe_sjoin_intersects(points_gdf, squares_gdf):
    joined = geodataframe_sjoin(points_gdf, squares_gdf, how="inner", predicate="intersects")
    assert is_geodataframe(joined)
    # Point(0,0) and Point(1,1) fall in z0; Point(5,5) in z1.
    assert set(joined["zone"]) == {"z0", "z1"}
    assert len(joined) == 3


def test_geodataframe_sjoin_nearest(points_gdf, squares_gdf):
    joined = geodataframe_sjoin_nearest(points_gdf, squares_gdf)
    assert is_geodataframe(joined)
    # nearest join keeps every left row
    assert len(joined) == len(points_gdf)
    assert "zone" in joined.columns


def test_geodataframe_clip(points_gdf):
    mask = gpd.GeoDataFrame(geometry=[_square(-1, -1, 3)], crs="EPSG:4326")
    clipped = geodataframe_clip(points_gdf, mask)
    assert is_geodataframe(clipped)
    # only the two points inside the [-1,2] square survive
    assert set(clipped["name"]) == {"a", "b"}


def test_geodataframe_overlay_intersection():
    left = gpd.GeoDataFrame(
        {"l": ["L"]}, geometry=[_square(0, 0, 2)], crs="EPSG:4326"
    )
    right = gpd.GeoDataFrame(
        {"r": ["R"]}, geometry=[_square(1, 1, 2)], crs="EPSG:4326"
    )
    res = geodataframe_overlay(left, right, how="intersection")
    assert is_geodataframe(res)
    assert len(res) == 1
    # intersection of two unit-offset 2x2 squares => 1x1 area
    assert res.geometry.iloc[0].area == pytest.approx(1.0)


# Frame transforms

def test_geodataframe_drop_duplicates_reset_index():
    gdf = gpd.GeoDataFrame(
        {"k": [1, 1, 2]},
        geometry=[Point(0, 0), Point(0, 0), Point(1, 1)],
        crs="EPSG:4326",
    )
    out = geodataframe_drop_duplicates_reset_index(gdf)
    assert len(out) == 2
    assert list(out.index) == [0, 1]


def test_geodataframe_group_size_named():
    gdf = gpd.GeoDataFrame(
        {"cat": ["x", "x", "y"]},
        geometry=[Point(0, 0), Point(1, 1), Point(2, 2)],
        crs="EPSG:4326",
    )
    s = geodataframe_group_size_named(gdf, by="cat", name="cnt")
    assert s.name == "cnt"
    assert s["x"] == 2
    assert s["y"] == 1


def test_geodataframe_group_aggregate_named():
    gdf = gpd.GeoDataFrame(
        {"cat": ["x", "x", "y"], "val": [10, 30, 5]},
        geometry=[Point(0, 0), Point(1, 1), Point(2, 2)],
        crs="EPSG:4326",
    )
    s = geodataframe_group_aggregate_named(gdf, by="cat", column="val", func="sum", name="tot")
    assert s.name == "tot"
    assert s["x"] == 40
    assert s["y"] == 5


def test_geoseries_first_value():
    gs = gpd.GeoSeries([Point(7, 7), Point(8, 8)])
    assert geoseries_first_value(gs).equals(Point(7, 7))


def test_active_geometry_value_get_and_set():
    gdf = gpd.GeoDataFrame(
        {"name": ["a", "b"]},
        geometry=[Point(0, 0), Point(1, 1)],
        crs="EPSG:4326",
    )
    assert active_geometry_value_at(gdf, 0).equals(Point(0, 0))
    set_active_geometry_value_at(gdf, 0, Point(9, 9))
    assert active_geometry_value_at(gdf, 0).equals(Point(9, 9))


# Introspection

def test_majority_geometry_type_points(points_gdf):
    majority, unique, srid = geodataframe_majority_geometry_type(points_gdf)
    assert majority == "Point"
    assert unique == {"Point"}
    assert srid == 4326


def test_majority_geometry_type_mixed_picks_most_common():
    gdf = gpd.GeoDataFrame(
        geometry=[Point(0, 0), Point(1, 1), _square(0, 0)],
        crs="EPSG:4326",
    )
    majority, unique, srid = geodataframe_majority_geometry_type(gdf)
    assert majority == "Point"
    assert unique == {"Point", "Polygon"}


def test_majority_geometry_type_plain_df_returns_defaults():
    majority, unique, srid = geodataframe_majority_geometry_type(pd.DataFrame({"a": [1]}))
    assert majority is None
    assert unique == set()
    assert srid is None


def test_majority_geometry_type_empty_gdf():
    gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    majority, unique, srid = geodataframe_majority_geometry_type(gdf)
    assert majority is None
    assert unique == set()


def test_majority_geometry_type_all_null_geoms():
    gdf = gpd.GeoDataFrame(geometry=[None, None], crs="EPSG:4326")
    majority, unique, srid = geodataframe_majority_geometry_type(gdf)
    # geometry present but no valid types -> majority None but srid still read
    assert majority is None
    assert unique == set()
    assert srid == 4326


def test_majority_geometry_type_no_crs():
    gdf = gpd.GeoDataFrame(geometry=[Point(0, 0)])
    majority, unique, srid = geodataframe_majority_geometry_type(gdf)
    assert majority == "Point"
    assert srid is None


def test_total_bounds(points_gdf):
    bounds = geodataframe_total_bounds(points_gdf)
    assert list(bounds) == [0.0, 0.0, 5.0, 5.0]


def test_total_bounds_plain_df_returns_none():
    assert geodataframe_total_bounds(pd.DataFrame({"a": [1]})) is None


def test_total_bounds_empty_gdf_returns_none():
    gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    assert geodataframe_total_bounds(gdf) is None


# to_geodataframe

def test_to_geodataframe_passthrough_for_existing_gdf(points_gdf):
    out = to_geodataframe(points_gdf)
    assert out is points_gdf


def test_to_geodataframe_wkt_autodetect_column():
    df = pd.DataFrame({"id": [1, 2], "geom": ["POINT (0 0)", "POINT (1 1)"]})
    gdf = to_geodataframe(df)
    assert is_geodataframe(gdf)
    assert gdf.geometry.name == "geom"
    assert gdf.geometry.iloc[0].equals(Point(0, 0))
    assert gdf.crs.to_epsg() == 4326


def test_to_geodataframe_explicit_column_and_crs():
    df = pd.DataFrame({"shape_wkt": ["POINT (2 3)"]})
    gdf = to_geodataframe(df, geom_column="shape_wkt", crs="EPSG:3857")
    assert gdf.geometry.name == "shape_wkt"
    assert gdf.crs.to_epsg() == 3857


def test_to_geodataframe_preserves_original_geom_column_name():
    df = pd.DataFrame({"the_geom": ["POINT (0 0)"], "x": [1]})
    gdf = to_geodataframe(df)
    assert gdf.geometry.name == "the_geom"
    assert "x" in gdf.columns


def test_to_geodataframe_does_not_mutate_input():
    df = pd.DataFrame({"geom": ["POINT (0 0)"]})
    original_value = df["geom"].iloc[0]
    to_geodataframe(df)
    # the source frame's column must still hold the raw WKT string
    assert df["geom"].iloc[0] == original_value
    assert isinstance(df["geom"].iloc[0], str)


def test_to_geodataframe_no_geometry_column_raises():
    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    with pytest.raises(ValueError, match="Nessuna colonna geometria"):
        to_geodataframe(df)


def test_to_geodataframe_explicit_missing_column_raises():
    df = pd.DataFrame({"geom": ["POINT (0 0)"]})
    with pytest.raises(ValueError, match="Nessuna colonna geometria"):
        to_geodataframe(df, geom_column="does_not_exist")


def test_to_geodataframe_no_valid_geometry_raises():
    df = pd.DataFrame({"geom": ["garbage", "still garbage"]})
    with pytest.raises(ValueError, match="Nessuna geometria valida"):
        to_geodataframe(df, geom_column="geom")


def test_to_geodataframe_truncated_wkt_message():
    # WKT prefix but no closing paren -> dedicated "WKT troncato" hint
    df = pd.DataFrame({"geom": ["POLYGON ((0 0"]})
    with pytest.raises(ValueError, match="WKT troncato"):
        to_geodataframe(df, geom_column="geom")


def test_to_geodataframe_reconstructs_csv_split_wkt():
    # pd.read_csv without quoting splits "POLYGON ((0 0, 1 0, 1 1, 0 0))"
    # across Unnamed columns; to_geodataframe rejoins them with commas.
    df = pd.DataFrame(
        {
            "geom": ["POLYGON ((0 0"],
            "Unnamed: 1": [" 1 0"],
            "Unnamed: 2": [" 1 1"],
            "Unnamed: 3": [" 0 0))"],
        }
    )
    gdf = to_geodataframe(df, geom_column="geom")
    assert is_geodataframe(gdf)
    # Unnamed fragment columns removed after reconstruction
    assert not any(str(c).startswith("Unnamed") for c in gdf.columns)
    assert gdf.geometry.iloc[0].geom_type == "Polygon"
    # triangle (0,0)-(1,0)-(1,1) => area 0.5
    assert gdf.geometry.iloc[0].area == pytest.approx(0.5)


# detect_geometry_column

def test_detect_geometry_column_exact_known_name():
    df = pd.DataFrame({"id": [1], "geometry": ["POINT (0 0)"]})
    assert detect_geometry_column(df) == "geometry"


def test_detect_geometry_column_case_insensitive_known_name():
    df = pd.DataFrame({"GEOM": ["POINT (0 0)"]})
    assert detect_geometry_column(df) == "GEOM"


def test_detect_geometry_column_substring_match():
    df = pd.DataFrame({"my_geom_col": ["POINT (0 0)"], "x": [1]})
    assert detect_geometry_column(df) == "my_geom_col"


def test_detect_geometry_column_by_wkt_content():
    # content-detection branch only inspects object-dtype columns, so force it
    # (recent pandas infers StringDtype which the code intentionally skips).
    df = pd.DataFrame(
        {"loc": pd.Series(["LINESTRING (0 0, 1 1)"], dtype=object), "n": [1]}
    )
    assert detect_geometry_column(df) == "loc"


def test_detect_geometry_column_by_geojson_content():
    df = pd.DataFrame(
        {"shp": pd.Series(['{"type": "Point", "coordinates": [0, 0]}'], dtype=object)}
    )
    assert detect_geometry_column(df) == "shp"


def test_detect_geometry_column_none_when_absent():
    df = pd.DataFrame({"a": ["hello", "world"], "b": [1, 2]})
    assert detect_geometry_column(df) is None


def test_detect_geometry_column_ignores_non_object_columns():
    df = pd.DataFrame({"num": [1, 2], "txt": ["plain", "text"]})
    assert detect_geometry_column(df) is None


def test_detect_geometry_column_invalid_json_not_matched():
    df = pd.DataFrame({"col": pd.Series(["{not valid json"], dtype=object)})
    assert detect_geometry_column(df) is None


# ensure_same_crs

def test_ensure_same_crs_reprojects_second():
    gdf1 = gpd.GeoDataFrame(geometry=[Point(0, 0)], crs="EPSG:4326")
    gdf2 = gpd.GeoDataFrame(geometry=[Point(0, 0)], crs="EPSG:3857")
    out1, out2 = ensure_same_crs(gdf1, gdf2)
    assert out1.crs == gdf1.crs
    assert out2.crs == gdf1.crs


def test_ensure_same_crs_noop_when_equal():
    gdf1 = gpd.GeoDataFrame(geometry=[Point(0, 0)], crs="EPSG:4326")
    gdf2 = gpd.GeoDataFrame(geometry=[Point(1, 1)], crs="EPSG:4326")
    out1, out2 = ensure_same_crs(gdf1, gdf2)
    assert out2 is gdf2  # unchanged object when CRS already matches


# extract_srid_from_column_type

def test_extract_srid_full_type():
    assert extract_srid_from_column_type("geometry:polygon:4326") == 4326


def test_extract_srid_other_code():
    assert extract_srid_from_column_type("geometry:point:3003") == 3003


def test_extract_srid_no_srid_component():
    assert extract_srid_from_column_type("geometry:polygon") is None


def test_extract_srid_non_geometry_type():
    assert extract_srid_from_column_type("str") is None


def test_extract_srid_empty_string():
    assert extract_srid_from_column_type("") is None


def test_extract_srid_non_numeric_component():
    assert extract_srid_from_column_type("geometry:polygon:abc") is None


# re-exported parse_geometry compatibility shim

def test_parse_geometry_reexport_parses_wkt():
    geom = parse_geometry("POINT (4 5)")
    assert geom.equals(Point(4, 5))


def test_parse_geometry_reexport_none_for_empty():
    assert parse_geometry("") is None
