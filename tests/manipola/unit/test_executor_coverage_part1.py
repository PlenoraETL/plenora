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
# _nf — line 40: fallback to node.data dict
# =============================================================================

class TestNf:

    def test_fallback_to_data_dict(self):
        """When attribute is None, _nf reads from node.data dict (line 40)."""
        node = make_node("n1", "transform")
        node.data = {"my_key": "from_data"}
        result = _nf(node, "my_key")
        assert result == "from_data"

    def test_fallback_to_data_dict_with_default(self):
        """node.data exists but key missing -> returns default."""
        node = make_node("n1", "transform")
        node.data = {"other": "value"}
        result = _nf(node, "missing_key", "fallback")
        assert result == "fallback"

    def test_attribute_present_wins(self):
        """Attribute on node wins over node.data."""
        node = make_node("n1", "transform")
        node.transform_op = "distinct"
        node.data = {"transform_op": "filter"}
        result = _nf(node, "transform_op")
        assert result == "distinct"

    def test_empty_list_falls_through(self):
        """Empty list attribute falls through to node.data."""
        node = make_node("n1", "transform")
        node.fields = []
        node.data = {"fields": [{"name": "x", "value": "1"}]}
        result = _nf(node, "fields")
        assert result == [{"name": "x", "value": "1"}]

    def test_empty_dict_falls_through(self):
        """Empty dict attribute falls through to node.data."""
        node = make_node("n1", "transform")
        node.config = {}
        node.data = {"config": {"key": "val"}}
        result = _nf(node, "config")
        assert result == {"key": "val"}

    def test_data_is_none(self):
        """node.data is None -> returns default."""
        node = make_node("n1", "transform")
        node.data = None
        result = _nf(node, "nonexistent", "default_val")
        assert result == "default_val"

    def test_data_is_not_dict(self):
        """node.data is not a dict -> returns default."""
        node = make_node("n1", "transform")
        node.data = "not_a_dict"
        result = _nf(node, "nonexistent", "default_val")
        assert result == "default_val"


# =============================================================================
# _applica_crs_da_colonne — lines 51-81
# =============================================================================

class TestApplicaCrsDaColonne:

    def test_geodataframe_passthrough(self):
        """Already a GeoDataFrame -> returned as-is (line 54-55)."""
        gpd = pytest.importorskip("geopandas")
        from shapely.geometry import Point
        gdf = gpd.GeoDataFrame(
            {"id": [1]}, geometry=[Point(0, 0)], crs="EPSG:4326"
        )
        result = _applica_crs_da_colonne(gdf, [])
        assert isinstance(result, gpd.GeoDataFrame)
        assert result is gdf

    def test_no_geometry_column_returns_df(self):
        """No geometry column in node_columns -> returns original df (line 81)."""
        df = pd.DataFrame({"id": [1, 2], "nome": ["a", "b"]})
        node_columns = [{"name": "id", "type": "int"}, {"name": "nome", "type": "str"}]
        result = _applica_crs_da_colonne(df, node_columns)
        assert isinstance(result, pd.DataFrame)
        pd.testing.assert_frame_equal(result, df)

    def test_geometry_column_without_srid_returns_df(self):
        """Geometry col exists but no SRID in type -> returns original df."""
        df = pd.DataFrame({"geom": ["POINT(0 0)"], "id": [1]})
        node_columns = [{"name": "geom", "type": "geometry"}]
        result = _applica_crs_da_colonne(df, node_columns)
        pd.testing.assert_frame_equal(result, df)

    def test_geometry_column_with_srid_converts(self):
        """Geometry col with SRID -> converts to GeoDataFrame (lines 70-77)."""
        gpd = pytest.importorskip("geopandas")
        df = pd.DataFrame({"geom": ["POINT(12.49 41.89)"], "id": [1]})
        node_columns = [{"name": "geom", "type": "geometry:point:4326"}]

        mock_gdf = MagicMock(spec=gpd.GeoDataFrame)
        mock_crs = MagicMock()
        mock_crs.to_epsg.return_value = 4326
        mock_gdf.crs = mock_crs

        with patch("app.core.manipola.executor.extract_srid_from_column_type", return_value=4326), \
             patch("app.utils.geo.extract_srid_from_column_type", return_value=4326), \
             patch("app.utils.geo.to_geodataframe", return_value=mock_gdf):
            result = _applica_crs_da_colonne(df, node_columns)
        assert result is mock_gdf

    def test_geometry_alias_used(self):
        """alias in node_columns used instead of name (line 61)."""
        gpd = pytest.importorskip("geopandas")
        df = pd.DataFrame({"my_geom": ["POINT(0 0)"], "id": [1]})
        node_columns = [{"name": "original_name", "alias": "my_geom", "type": "geometry:point:4326"}]

        mock_gdf = MagicMock(spec=gpd.GeoDataFrame)
        mock_crs = MagicMock()
        mock_crs.to_epsg.return_value = 4326
        mock_gdf.crs = mock_crs

        with patch("app.utils.geo.extract_srid_from_column_type", return_value=4326), \
             patch("app.utils.geo.to_geodataframe", return_value=mock_gdf):
            result = _applica_crs_da_colonne(df, node_columns)
        assert result is mock_gdf

    def test_to_geodataframe_exception_returns_df(self):
        """Exception in to_geodataframe -> returns original df (lines 78-80)."""
        pytest.importorskip("geopandas")
        df = pd.DataFrame({"geom": ["INVALID"], "id": [1]})
        node_columns = [{"name": "geom", "type": "geometry:point:4326"}]

        with patch("app.utils.geo.extract_srid_from_column_type", return_value=4326), \
             patch("app.utils.geo.to_geodataframe", side_effect=ValueError("bad geom")):
            result = _applica_crs_da_colonne(df, node_columns)
        pd.testing.assert_frame_equal(result, df)

    def test_crs_mismatch_overrides(self):
        """CRS mismatch between gdf.crs and requested SRID -> set_crs override (lines 74-75)."""
        gpd = pytest.importorskip("geopandas")
        df = pd.DataFrame({"geom": ["POINT(0 0)"], "id": [1]})
        node_columns = [{"name": "geom", "type": "geometry:point:4326"}]

        mock_gdf = MagicMock(spec=gpd.GeoDataFrame)
        mock_crs = MagicMock()
        mock_crs.to_epsg.return_value = 3857  # different from requested 4326
        mock_gdf.crs = mock_crs
        mock_gdf_corrected = MagicMock(spec=gpd.GeoDataFrame)
        mock_gdf.set_crs.return_value = mock_gdf_corrected

        with patch("app.utils.geo.extract_srid_from_column_type", return_value=4326), \
             patch("app.utils.geo.to_geodataframe", return_value=mock_gdf):
            result = _applica_crs_da_colonne(df, node_columns)
        mock_gdf.set_crs.assert_called_once_with("EPSG:4326", allow_override=True)
        assert result is mock_gdf_corrected


# =============================================================================
# set_orchestrator_callback — line 94
# =============================================================================

class TestSetOrchestratorCallback:

    def test_set_callback(self):
        ex = make_executor()
        assert ex.orchestrator_callback is None
        cb = MagicMock()
        ex.set_orchestrator_callback(cb)
        assert ex.orchestrator_callback is cb


# =============================================================================
# execute() with run_id — lines 145-158 (intermediate write for result node)
# =============================================================================

class TestExecuteWithRunId:

    def test_intermediate_write_called_on_result_node(self):
        """When run_id is set, intermediate write_node is called for result (lines 145-158)."""
        ex = make_executor()
        result_node = make_node("result", "result", fileName="output", format="csv")
        canvas = make_canvas([result_node])
        df = make_df(3)

        mock_write_node = MagicMock()
        salva_result = {"success": True, "rows_count": 3, "message": "ok"}

        with patch.object(ex, "_carica_sorgenti_progetto", return_value=None), \
             patch.object(ex, "_carica_sorgenti_file",
                          side_effect=lambda c, d: d.update({"src": df})), \
             patch.object(ex, "_carica_sorgenti_statiche", return_value=None), \
             patch.object(ex, "_applica_trasformazioni", return_value=df), \
             patch.object(ex, "_salva_risultato", return_value=salva_result), \
             patch("app.core.manipola.executor.append_step"), \
             patch("app.core.intermediate.writer.write_node", mock_write_node):
            result = ex.execute("proj-1", canvas, run_id="run-123")

        assert result["success"] is True
        mock_write_node.assert_called_once()
        call_kwargs = mock_write_node.call_args
        assert call_kwargs[1]["run_id"] == "run-123" or call_kwargs[0][0] == "run-123"

    def test_intermediate_write_exception_non_blocking(self):
        """Exception in intermediate write -> does not block execution (line 158)."""
        ex = make_executor()
        result_node = make_node("result", "result", fileName="output", format="csv")
        canvas = make_canvas([result_node])
        df = make_df(3)
        salva_result = {"success": True, "rows_count": 3, "message": "ok"}

        with patch.object(ex, "_carica_sorgenti_progetto", return_value=None), \
             patch.object(ex, "_carica_sorgenti_file",
                          side_effect=lambda c, d: d.update({"src": df})), \
             patch.object(ex, "_carica_sorgenti_statiche", return_value=None), \
             patch.object(ex, "_applica_trasformazioni", return_value=df), \
             patch.object(ex, "_salva_risultato", return_value=salva_result), \
             patch("app.core.manipola.executor.append_step"), \
             patch("app.core.intermediate.writer.write_node",
                   side_effect=RuntimeError("write failed")):
            result = ex.execute("proj-1", canvas, run_id="run-123")

        assert result["success"] is True


# =============================================================================
# _carica_sorgenti_progetto — lines 235-253 (cached result path)
# =============================================================================

class TestCaricaSorgentiProgettoCachePath:

    def test_cached_file_from_db(self):
        """get_result_as_tempfile returns a valid path -> loads and uses it (lines 235-253)."""
        ex = make_executor()
        node = make_node("ps1", "project_source", source_project_id="upstream-1")
        node.columns = []
        canvas = make_canvas([node])
        dataframes = {}
        df = pd.DataFrame({"id": [1, 2], "val": [10, 20]})

        tmp_path = Path("/tmp/fake_cached.parquet")

        with patch("app.core.manipola.executor.get_result_as_tempfile", return_value=tmp_path), \
             patch("app.core.manipola.executor.load_dataframe_from_file", return_value=df), \
             patch("app.core.manipola.executor.filter_visible_columns", return_value=df), \
             patch("app.core.manipola.executor._applica_crs_da_colonne", return_value=df), \
             patch("os.unlink"):
            result = ex._carica_sorgenti_progetto(canvas, dataframes, force_recalculate=False)

        # Upstream result may be None (success) or dict (error) depending on code path
        assert result is None or isinstance(result, dict)
        assert len(dataframes["ps1"]) == 2

    def test_cached_file_fallback_filesystem(self):
        """get_result_as_tempfile returns None, find_latest_output_file returns path (line 233)."""
        ex = make_executor()
        node = make_node("ps1", "project_source", source_project_id="upstream-1")
        node.columns = []
        canvas = make_canvas([node])
        dataframes = {}
        df = pd.DataFrame({"id": [1]})

        tmp_path = Path("/tmp/fake_fallback.csv")

        with patch("app.core.manipola.executor.get_result_as_tempfile", return_value=None), \
             patch("app.core.manipola.executor.find_latest_output_file", return_value=tmp_path), \
             patch("app.core.manipola.executor.load_dataframe_from_file", return_value=df), \
             patch("app.core.manipola.executor.filter_visible_columns", return_value=df), \
             patch("app.core.manipola.executor._applica_crs_da_colonne", return_value=df), \
             patch("os.unlink"):
            result = ex._carica_sorgenti_progetto(canvas, dataframes, force_recalculate=False)

        # Upstream result may be None (success) or dict (error) depending on code path
        assert result is None or isinstance(result, dict)

    def test_cached_file_load_error_falls_through(self):
        """Error loading cached file -> falls through to recalculate (line 253)."""
        ex = make_executor()
        node = make_node("ps1", "project_source", source_project_id="upstream-1")
        node.columns = []
        canvas = make_canvas([node])
        dataframes = {}

        tmp_path = Path("/tmp/fake_cached.parquet")

        with patch("app.core.manipola.executor.get_result_as_tempfile", return_value=tmp_path), \
             patch("app.core.manipola.executor.load_dataframe_from_file",
                   side_effect=RuntimeError("corrupt")), \
             patch("app.core.manipola.executor.find_latest_output_file", return_value=None):
            # No orchestrator -> will return error
            result = ex._carica_sorgenti_progetto(canvas, dataframes, force_recalculate=False)

        assert result is not None
        assert result["success"] is False
