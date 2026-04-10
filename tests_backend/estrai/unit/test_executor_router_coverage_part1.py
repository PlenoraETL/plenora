"""
tests/estrai/unit/test_executor_router_coverage_part1.py
========================================================
Test coverage per router.py e executor.py (parte 1):
  - _traduci_errore_sql(), _is_user_error_message()
  - extract_srid_from_canvas()
  - Router endpoints (generate_sql, translate, preview, supported_functions)
  - ArcGIS execute path, _execute_feature_service
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

pytestmark = pytest.mark.unit

from app.core.estrai.executor import (
    EstraiExecutor,
    _traduci_errore_sql,
    _is_user_error_message,
)
from app.core.estrai.router import functions_router, sql_router, init_estrai
from app.shared.models import Node, Column, Edge, Position, CanvasState


_POS = Position(x=0, y=0)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_canvas(nodes, edges=None):
    return CanvasState(nodes=nodes, edges=edges or [])


def make_executor(features_service=None):
    return EstraiExecutor(
        project_service=MagicMock(),
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
        features_service=features_service,
    )




# =============================================================================
# EXECUTOR.PY — ArcGIS execute path (line 186) & _execute_feature_service
# =============================================================================

class TestExecuteArcGISPath:
    """Copertura linee 183-186: percorso ArcGIS in execute()."""

    def test_execute_arcgis_calls_execute_feature_service(self):
        """conn_type == 'arcgis' -> chiama _execute_feature_service."""
        fs = MagicMock()
        ex = make_executor(features_service=fs)
        ex.project_service.get_project_metadata.return_value = MagicMock(
            default_connection_id="arcgis-1"
        )
        conn = MagicMock(id="arcgis-1")
        ex.connection_service.get_connection.return_value = None
        fs.get_connection.return_value = conn

        result_node = Node(
            id="r1", type="result", position=_POS,
            fileName="out", format="csv",
        )
        feature_node = Node(
            id="fs1", type="feature_source", position=_POS,
            layer_url="https://example.com/layer/0",
            geometry_type="esriGeometryPolygon",
            columns=[Column(name="name", type="str", visible=True),
                     Column(name="geometry", type="geometry:polygon", visible=True)],
        )
        canvas = make_canvas([feature_node, result_node])
        fs.execute_query.return_value = {"success": True, "columns": ["name"]}

        res = ex.execute("proj-1", canvas)
        assert res["success"] is True
        fs.execute_query.assert_called_once()

    def test_execute_arcgis_no_feature_nodes(self):
        """ArcGIS path ma nessun nodo feature_source -> errore."""
        fs = MagicMock()
        ex = make_executor(features_service=fs)
        ex.project_service.get_project_metadata.return_value = MagicMock(
            default_connection_id="arcgis-1"
        )
        conn = MagicMock(id="arcgis-1")
        ex.connection_service.get_connection.return_value = None
        fs.get_connection.return_value = conn

        result_node = Node(id="r1", type="result", position=_POS)
        canvas = make_canvas([result_node])

        res = ex.execute("proj-1", canvas)
        assert res["success"] is False
        assert "Feature Source" in res["message"]


# =============================================================================
# EXECUTOR.PY — _execute_feature_service (lines 243-294)
# =============================================================================

class TestExecuteFeatureService:

    def _make_arcgis_executor(self):
        fs = MagicMock()
        ex = make_executor(features_service=fs)
        return ex, fs

    def test_feature_service_success_with_geometry(self):
        ex, fs = self._make_arcgis_executor()
        conn = MagicMock(id="c1")
        result_node = Node(
            id="r1", type="result", position=_POS,
            fileName="output", format="geojson", geomColumn="geometry",
        )
        feature_node = Node(
            id="fs1", type="feature_source", position=_POS,
            layer_url="https://host/layer/0",
            geometry_type="esriGeometryPoint",
            where="STATUS='A'",
            columns=[Column(name="id", type="int", visible=True),
                     Column(name="geometry", type="geometry:point", visible=True)],
        )
        canvas = make_canvas([feature_node, result_node])

        fs.execute_query.return_value = {"success": True, "columns": ["id", "geometry"]}

        res = ex._execute_feature_service("proj-1", canvas, result_node, conn)
        assert res["success"] is True
        assert "ArcGIS REST" in res["query"]
        call_kw = fs.execute_query.call_args
        assert call_kw.kwargs["return_geometry"] is True
        assert call_kw.kwargs["where"] == "STATUS='A'"

    def test_feature_service_geometry_hidden(self):
        """Geometry colonna nascosta -> return_geometry=False."""
        ex, fs = self._make_arcgis_executor()
        conn = MagicMock(id="c1")
        result_node = Node(id="r1", type="result", position=_POS)
        feature_node = Node(
            id="fs1", type="feature_source", position=_POS,
            layer_url="https://host/layer/0",
            geometry_type="esriGeometryPolygon",
            columns=[Column(name="name", type="str", visible=True),
                     Column(name="geometry", type="geometry:polygon", visible=False)],
        )
        canvas = make_canvas([feature_node, result_node])
        fs.execute_query.return_value = {"success": True, "columns": ["name"]}

        res = ex._execute_feature_service("proj-1", canvas, result_node, conn)
        call_kw = fs.execute_query.call_args
        assert call_kw.kwargs["return_geometry"] is False

    def test_feature_service_with_limit(self):
        """result_node.limit -> max_features."""
        ex, fs = self._make_arcgis_executor()
        conn = MagicMock(id="c1")
        result_node = Node(
            id="r1", type="result", position=_POS, limit=500,
        )
        feature_node = Node(
            id="fs1", type="feature_source", position=_POS,
            layer_url="https://host/layer/0",
        )
        canvas = make_canvas([feature_node, result_node])
        fs.execute_query.return_value = {"success": True, "columns": []}

        ex._execute_feature_service("proj-1", canvas, result_node, conn)
        call_kw = fs.execute_query.call_args
        assert call_kw.kwargs["max_features"] == 500

    def test_feature_service_exception(self):
        """Eccezione nel features_service.execute_query -> errore gestito."""
        ex, fs = self._make_arcgis_executor()
        conn = MagicMock(id="c1")
        result_node = Node(id="r1", type="result", position=_POS)
        feature_node = Node(
            id="fs1", type="feature_source", position=_POS,
            layer_url="https://host/layer/0",
        )
        canvas = make_canvas([feature_node, result_node])
        fs.execute_query.side_effect = RuntimeError("REST error")

        res = ex._execute_feature_service("proj-1", canvas, result_node, conn)
        assert res["success"] is False
        assert "REST error" in res["message"]

    def test_feature_service_where_default(self):
        """where assente -> default '1=1'."""
        ex, fs = self._make_arcgis_executor()
        conn = MagicMock(id="c1")
        result_node = Node(id="r1", type="result", position=_POS)
        feature_node = Node(
            id="fs1", type="feature_source", position=_POS,
            layer_url="https://host/layer/0",
        )
        canvas = make_canvas([feature_node, result_node])
        fs.execute_query.return_value = {"success": True, "columns": []}

        ex._execute_feature_service("proj-1", canvas, result_node, conn)
        call_kw = fs.execute_query.call_args
        assert call_kw.kwargs["where"] == "1=1"
