"""
tests/manipola/unit/test_executor_coverage.py
=============================================
Additional unit tests targeting uncovered lines in executor.py.

Missing lines: 40, 51-81, 94, 145-158, 235-253, 267-274, 287-305,
310-340, 405, 410, 422, 429, 457-471, 589-592, 610, 647-649,
657-691, 698, 718-719, 751, 769-777, 781, 786, 793-794
"""

import pytest
import pandas as pd
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

pytestmark = pytest.mark.unit

from app.core.manipola.executor import ManipolaExecutor, _nf, _applica_crs_da_colonne
from app.shared.models import Node, Edge, Column, Position, CanvasState


# ─── Helpers ──────────────────────────────────────────────────────────────────

_POS = Position(x=0, y=0)


def make_executor(orchestrator=None):
    return ManipolaExecutor(
        project_service=MagicMock(),
        orchestrator_callback=orchestrator,
    )


def make_node(node_id, node_type, columns=None, calc_cols=None, **kwargs):
    cols = [
        Column(
            name=c["name"],
            type=c.get("type", "str"),
            visible=True,
            alias=c.get("alias"),
        )
        for c in (columns or [])
    ]
    n = Node(
        id=node_id,
        type=node_type,
        position=_POS,
        columns=cols,
        calculated_columns=calc_cols or [],
    )
    for k, v in kwargs.items():
        setattr(n, k, v)
    return n


def make_edge(src, tgt, edge_id=None, handle=None):
    eid = edge_id or f"{src}-{tgt}"
    if handle:
        # Encode targetHandle in edge ID since Edge model doesn't have the field
        eid = f"{src}-{tgt}-{handle}"
    return Edge(id=eid, source=src, target=tgt)


def make_canvas(nodes, edges=None):
    return CanvasState(nodes=nodes, edges=edges or [])


def make_df(n=5):
    return pd.DataFrame({
        "id": range(n),
        "nome": [f"item_{i}" for i in range(n)],
        "valore": [float(i * 10) for i in range(n)],
    })




# =============================================================================
# _rileva_geometria_da_contenuto — lines 769-777, 781, 786, 793-794
# =============================================================================

class TestRilevaGeometriaDaContenuto:

    def test_known_geometry_column_name_with_wkt(self):
        """Lines 769-777: col named 'geom' with WKT string -> 'geometry'."""
        ex = make_executor()
        df = pd.DataFrame({"geom": ["POINT(1 2)", "LINESTRING(0 0, 1 1)"]})
        result = ex._rileva_geometria_da_contenuto(df, "geom")
        assert result == "geometry"

    def test_known_geometry_column_name_with_shapely(self):
        """Lines 773-774: col named 'geometry' with Shapely object -> 'geometry'."""
        pytest.importorskip("shapely")
        from shapely.geometry import Point
        ex = make_executor()
        df = pd.DataFrame({"geometry": [Point(0, 0), Point(1, 1)]})
        result = ex._rileva_geometria_da_contenuto(df, "geometry")
        assert result == "geometry"

    def test_generic_column_with_shapely(self):
        """Line 786: non-geometry-named col with Shapely object -> 'geometry'."""
        pytest.importorskip("shapely")
        from shapely.geometry import Point
        ex = make_executor()
        df = pd.DataFrame({"my_col": [Point(0, 0)]})
        result = ex._rileva_geometria_da_contenuto(df, "my_col")
        assert result == "geometry"

    def test_generic_column_with_wkt_string(self):
        """Lines 790-792: non-geometry-named col with WKT -> 'geometry'."""
        ex = make_executor()
        df = pd.DataFrame({"shape_data": ["POLYGON((0 0,1 0,1 1,0 1,0 0))"]})
        result = ex._rileva_geometria_da_contenuto(df, "shape_data")
        assert result == "geometry"

    def test_generic_column_with_multipolygon(self):
        ex = make_executor()
        df = pd.DataFrame({"area": ["MULTIPOLYGON(((0 0,1 0,1 1,0 0)))"]})
        result = ex._rileva_geometria_da_contenuto(df, "area")
        assert result == "geometry"

    def test_non_geometry_string_returns_str(self):
        """Line 792: regular string -> 'str'."""
        ex = make_executor()
        df = pd.DataFrame({"note": ["hello world", "foo bar"]})
        result = ex._rileva_geometria_da_contenuto(df, "note")
        assert result == "str"

    def test_empty_column_returns_str(self):
        """Line 781: column is all NaN -> 'str'."""
        ex = make_executor()
        df = pd.DataFrame({"empty_col": [None, None]})
        result = ex._rileva_geometria_da_contenuto(df, "empty_col")
        assert result == "str"

    def test_exception_returns_str(self):
        """Lines 793-794: exception -> 'str'."""
        ex = make_executor()
        df = MagicMock()
        df.__getitem__ = MagicMock(side_effect=RuntimeError("error"))
        result = ex._rileva_geometria_da_contenuto(df, "col")
        assert result == "str"

    def test_known_name_non_wkt_content(self):
        """Lines 776-777: col named 'shape' but content not WKT -> falls through."""
        ex = make_executor()
        df = pd.DataFrame({"shape": ["rectangle", "circle"]})
        result = ex._rileva_geometria_da_contenuto(df, "shape")
        # 'rectangle' starts with R, not matching P/L/M/G quick check
        assert result == "str"

    def test_the_geom_column_name(self):
        """Lines 768-769: 'the_geom' is a known geometry column name."""
        ex = make_executor()
        df = pd.DataFrame({"the_geom": ["POINT(0 0)"]})
        result = ex._rileva_geometria_da_contenuto(df, "the_geom")
        assert result == "geometry"

    def test_generic_column_empty_returns_str(self):
        """Line 780-781: generic col name, all NaN -> 'str'."""
        ex = make_executor()
        df = pd.DataFrame({"data": [None, None, None]})
        result = ex._rileva_geometria_da_contenuto(df, "data")
        assert result == "str"

    def test_geometrycollection_detected(self):
        """GEOMETRYCOLLECTION prefix detected."""
        ex = make_executor()
        df = pd.DataFrame({"gc": ["GEOMETRYCOLLECTION(POINT(0 0))"]})
        result = ex._rileva_geometria_da_contenuto(df, "gc")
        assert result == "geometry"
