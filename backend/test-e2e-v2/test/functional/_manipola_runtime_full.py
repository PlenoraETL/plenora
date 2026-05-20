"""Runtime MANIPOLA coverage helpers for the E2E v2 host suite."""

from __future__ import annotations

import ast
import contextlib
import hashlib
import logging
import os
import sys
import types
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, MultiPoint, Point, Polygon

from _e2e import assert_equal, assert_true, fail


BACKEND_ROOT = Path(__file__).resolve().parents[3]
TEST_ROOT = Path(__file__).resolve().parent

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    import psycopg2  # noqa: F401
except ModuleNotFoundError:
    psycopg2_stub = types.ModuleType("psycopg2")
    psycopg2_extras_stub = types.ModuleType("psycopg2.extras")
    psycopg2_pool_stub = types.ModuleType("psycopg2.pool")
    psycopg2_stub.extras = psycopg2_extras_stub
    psycopg2_stub.pool = psycopg2_pool_stub
    sys.modules["psycopg2"] = psycopg2_stub
    sys.modules["psycopg2.extras"] = psycopg2_extras_stub
    sys.modules["psycopg2.pool"] = psycopg2_pool_stub

from app.core.manipola.plugin_executor import get_executor  # noqa: E402


NEW_RUNTIME_TRANSFORMS = {
    "concat",
    "cross_join",
    "geo_area",
    "geo_boundary",
    "geo_bounds_extractor",
    "geo_buffer",
    "geo_centroid",
    "geo_clean_topology",
    "geo_clip",
    "geo_convex_hull",
    "geo_count_points_in_polygons",
    "geo_difference",
    "geo_dissolve",
    "geo_distance",
    "geo_envelope",
    "geo_explode",
    "geo_from_coords",
    "geo_intersection",
    "geo_length",
    "geo_line_builder",
    "geo_make_valid",
    "geo_nearest",
    "geo_overlay",
    "geo_perimeter",
    "geo_point_on_surface",
    "geo_polygon_builder",
    "geo_reproject",
    "geo_simplify",
    "geo_symmetric_difference",
    "geo_to_wkt",
    "geo_union",
    "geo_vertex_count",
    "geo_voronoi",
    "geo_within",
    "join",
    "mask_data",
    "md5_hash",
    "sjoin",
    "statistics",
    "transpose",
}


def run_silent(fn) -> None:
    logging.disable(logging.CRITICAL)
    with open(os.devnull, "w", encoding="utf-8") as devnull:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            fn()


def run_transform(transform_id: str, inputs: list[Any], params: dict[str, Any]) -> Any:
    return get_executor().execute_with_params(transform_id, inputs, params)


def assert_columns(frame: Any, columns: Iterable[str], message: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    assert_equal(missing, [], message)


def assert_geom_types(frame: Any, expected: Iterable[str], message: str) -> None:
    actual = [geom.geom_type if geom is not None else None for geom in frame.geometry]
    assert_equal(actual, list(expected), message)


def base_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"id": 1, "group": "A", "qty": 2, "price": 10.0, "name": "Alice", "email": "alice@example.test"},
            {"id": 2, "group": "A", "qty": 3, "price": 20.0, "name": "Bob", "email": "bob@example.test"},
            {"id": 3, "group": "B", "qty": 5, "price": 30.0, "name": "Carol", "email": "carol@example.test"},
        ]
    )


def point_frame() -> gpd.GeoDataFrame:
    rows = [
        {"id": 1, "group": "north", "seq": 1, "value": 10, "geom": Point(0.0, 0.0)},
        {"id": 2, "group": "north", "seq": 2, "value": 20, "geom": Point(1.0, 0.0)},
        {"id": 3, "group": "north", "seq": 3, "value": 30, "geom": Point(1.0, 1.0)},
        {"id": 4, "group": "south", "seq": 1, "value": 40, "geom": Point(3.0, 3.0)},
    ]
    return gpd.GeoDataFrame(rows, geometry="geom", crs="EPSG:4326")


def polygon_frame() -> gpd.GeoDataFrame:
    rows = [
        {
            "pid": "p1",
            "zone": "A",
            "score": 100,
            "geom": Polygon([(-0.5, -0.5), (1.5, -0.5), (1.5, 1.5), (-0.5, 1.5)]),
        },
        {
            "pid": "p2",
            "zone": "B",
            "score": 200,
            "geom": Polygon([(2.5, 2.5), (3.5, 2.5), (3.5, 3.5), (2.5, 3.5)]),
        },
    ]
    return gpd.GeoDataFrame(rows, geometry="geom", crs="EPSG:4326")


def overlapping_polygon_frame() -> gpd.GeoDataFrame:
    rows = [
        {"pid": "a", "zone": "A", "geom": Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])},
        {"pid": "b", "zone": "B", "geom": Polygon([(1, 1), (3, 1), (3, 3), (1, 3)])},
    ]
    return gpd.GeoDataFrame(rows, geometry="geom", crs="EPSG:4326")


def line_frame() -> gpd.GeoDataFrame:
    rows = [
        {"lid": 1, "geom": LineString([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])},
        {"lid": 2, "geom": LineString([(2.0, 2.0), (3.0, 3.0)])},
    ]
    return gpd.GeoDataFrame(rows, geometry="geom", crs="EPSG:4326")


def multipoint_frame() -> gpd.GeoDataFrame:
    rows = [{"id": 1, "geom": MultiPoint([(0, 0), (1, 1), (2, 2)])}]
    return gpd.GeoDataFrame(rows, geometry="geom", crs="EPSG:4326")


def invalid_polygon_frame() -> gpd.GeoDataFrame:
    bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
    return gpd.GeoDataFrame([{"id": 1, "geom": bowtie}], geometry="geom", crs="EPSG:4326")


def md5_expected(*values: Any, normalize: bool = True) -> str:
    parts = []
    for value in values:
        text = "" if value is None else str(value)
        parts.append(text.strip().lower() if normalize else text)
    return hashlib.md5("\x1f".join(parts).encode("utf-8")).hexdigest()


def registry_transform_ids() -> set[str]:
    return set(get_executor().get_registry().keys())


def test_006_runtime_ids() -> set[str]:
    text = (TEST_ROOT / "test_006.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    ids: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "_run"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                ids.add(node.args[0].value)
            self.generic_visit(node)

    Visitor().visit(tree)
    return ids


def assert_non_empty_exception(transform_id: str, exc: Exception) -> None:
    message = str(exc)
    assert_true(message or exc.__class__.__name__, f"{transform_id} negative path must raise a readable exception")


def fail_case(case_name: str, exc: Exception) -> None:
    fail(f"{case_name} failed with {exc.__class__.__name__}: {exc}")
