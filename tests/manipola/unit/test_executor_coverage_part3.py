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
# _esegui_trasformazione — lines 589-592
# =============================================================================

class TestEseguiTrasformazione:

    def test_no_transform_op_passthrough(self):
        """Line 590-591: no transform_op -> returns first input."""
        ex = make_executor()
        node = make_node("t1", "transform")
        # No transform_op set
        df = pd.DataFrame({"a": [1, 2]})
        result = ex._esegui_trasformazione(node, [df])
        pd.testing.assert_frame_equal(result, df)

    def test_no_transform_op_no_inputs_empty_df(self):
        """Line 591: no transform_op, no inputs -> empty DataFrame."""
        ex = make_executor()
        node = make_node("t1", "transform")
        result = ex._esegui_trasformazione(node, [])
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0

    def test_with_transform_op_delegates(self):
        """Line 592: with transform_op -> delegates to get_executor().execute()."""
        ex = make_executor()
        node = make_node("t1", "transform", transform_op="distinct")
        df_in = pd.DataFrame({"a": [1, 1, 2]})
        df_out = pd.DataFrame({"a": [1, 2]})

        mock_plugin = MagicMock()
        mock_plugin.execute.return_value = df_out

        with patch("app.core.manipola.executor.get_executor", return_value=mock_plugin):
            result = ex._esegui_trasformazione(node, [df_in])

        mock_plugin.execute.assert_called_once_with("distinct", [df_in], node)
        pd.testing.assert_frame_equal(result, df_out)


# =============================================================================
# _applica_group_by — lines 610, 647-649
# =============================================================================

class TestApplicaGroupByExtended:

    def _result_node(self, enabled=True, group_cols=None, aggregations=None):
        node = make_node("result", "result")
        node.group_by_enabled = enabled
        node.group_by_columns = group_cols or []
        node.aggregations = aggregations or []
        return node

    def test_group_cols_not_in_df_returns_unchanged(self):
        """Line 610: all group_cols missing from df -> returns df unchanged."""
        ex = make_executor()
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        node = self._result_node(
            enabled=True,
            group_cols=["nonexistent_col"],
            aggregations=[{"column": "a", "function": "sum", "alias": "tot"}],
        )
        result = ex._applica_group_by(df, node)
        pd.testing.assert_frame_equal(result, df)

    def test_group_by_exception_returns_df(self):
        """Lines 647-649: exception during GROUP BY -> returns original df."""
        ex = make_executor()
        df = pd.DataFrame({"comune": ["Roma", "Milano"], "valore": [100, 200]})
        node = self._result_node(
            enabled=True,
            group_cols=["comune"],
            aggregations=[{"column": "valore", "function": "sum", "alias": "tot"}],
        )

        with patch.object(df, "groupby", side_effect=RuntimeError("groupby error")):
            result = ex._applica_group_by(df, node)
        pd.testing.assert_frame_equal(result, df)

    def test_only_count_no_column_aggs(self):
        """Lines 634-636: only count aggregations, no column aggs -> uses size()."""
        ex = make_executor()
        df = pd.DataFrame({
            "city": ["Roma", "Roma", "Milano", "Milano", "Milano"],
            "val": [1, 2, 3, 4, 5],
        })
        node = self._result_node(
            enabled=True,
            group_cols=["city"],
            aggregations=[{"column": None, "function": "count", "alias": "cnt"}],
        )
        result = ex._applica_group_by(df, node)
        assert "cnt" in result.columns
        assert "city" in result.columns
        roma_cnt = result.loc[result["city"] == "Roma", "cnt"].iloc[0]
        milano_cnt = result.loc[result["city"] == "Milano", "cnt"].iloc[0]
        assert roma_cnt == 2
        assert milano_cnt == 3

    def test_mean_aggregation(self):
        ex = make_executor()
        df = pd.DataFrame({
            "grp": ["A", "A", "B"],
            "val": [10.0, 20.0, 30.0],
        })
        node = self._result_node(
            enabled=True,
            group_cols=["grp"],
            aggregations=[{"column": "val", "function": "mean", "alias": "avg"}],
        )
        result = ex._applica_group_by(df, node)
        assert "avg" in result.columns
        a_avg = result.loc[result["grp"] == "A", "avg"].iloc[0]
        assert abs(a_avg - 15.0) < 0.01

    def test_no_valid_aggregation_returns_df(self):
        """Lines 637-639: no count and no valid column agg -> returns df."""
        ex = make_executor()
        df = pd.DataFrame({"grp": ["A", "B"], "val": [1, 2]})
        node = self._result_node(
            enabled=True,
            group_cols=["grp"],
            aggregations=[{"column": "nonexistent", "function": "sum", "alias": "x"}],
        )
        result = ex._applica_group_by(df, node)
        pd.testing.assert_frame_equal(result, df)


# =============================================================================
# _salva_risultato — lines 657-691
# =============================================================================

class TestSalvaRisultato:

    def test_salva_successo(self):
        """Lines 657-689: successful save path."""
        ex = make_executor()
        result_node = make_node("result", "result", fileName="output", format="csv")
        df = make_df(5)
        canvas = make_canvas([
            make_node("src", "file_source", columns=[{"name": "id", "type": "int"}])
        ])

        saved_path = Path("/tmp/output.csv")

        with patch("app.core.manipola.executor.detect_geometry_column", return_value=None), \
             patch.object(ex, "_estrai_srid_da_canvas", return_value=None), \
             patch("app.core.manipola.executor.get_temp_output_path", return_value=saved_path), \
             patch("app.core.manipola.executor.save_dataframe", return_value=saved_path), \
             patch("app.core.manipola.executor.save_result_blob") as mock_blob:
            mock_metadata = MagicMock()
            mock_metadata.group_id = "grp-1"
            ex.project_service.get_project_metadata.return_value = mock_metadata

            result = ex._salva_risultato("proj-1", result_node, df, canvas)

        assert result["success"] is True
        assert result["rows_count"] == 5
        assert result["format"] == "csv"
        mock_blob.assert_called_once()

    def test_salva_errore(self):
        """Line 691: save_dataframe raises -> error dict returned."""
        ex = make_executor()
        result_node = make_node("result", "result", fileName="output", format="csv")
        df = make_df(3)

        with patch("app.core.manipola.executor.detect_geometry_column", return_value=None), \
             patch.object(ex, "_estrai_srid_da_canvas", return_value=None), \
             patch("app.core.manipola.executor.get_temp_output_path",
                   return_value=Path("/tmp/output.csv")), \
             patch("app.core.manipola.executor.save_dataframe",
                   side_effect=RuntimeError("disk full")):
            result = ex._salva_risultato("proj-1", result_node, df)

        assert result["success"] is False
        assert "Errore salvataggio" in result["message"]

    def test_salva_with_srid(self):
        """Lines 665-668: canvas has SRID -> uses it for CRS."""
        ex = make_executor()
        result_node = make_node("result", "result", fileName="out", format="geojson")
        result_node.geomColumn = "geom"
        df = pd.DataFrame({"geom": ["POINT(0 0)"], "id": [1]})

        saved_path = Path("/tmp/out.geojson")
        canvas = make_canvas([
            make_node("src", "file_source",
                      columns=[{"name": "geom", "type": "geometry:point:32632"}])
        ])

        with patch("app.core.manipola.executor.detect_geometry_column", return_value="geom"), \
             patch.object(ex, "_estrai_srid_da_canvas", return_value=32632), \
             patch("app.core.manipola.executor.get_temp_output_path", return_value=saved_path), \
             patch("app.core.manipola.executor.save_dataframe", return_value=saved_path) as mock_save, \
             patch("app.core.manipola.executor.save_result_blob"):
            ex.project_service.get_project_metadata.return_value = None
            result = ex._salva_risultato("proj-1", result_node, df, canvas)

        assert result["success"] is True
        # Check CRS was passed as EPSG:32632
        call_args = mock_save.call_args
        assert call_args[0][4] == "EPSG:32632" or call_args[1].get("crs") == "EPSG:32632" \
            or "32632" in str(call_args)

    def test_salva_shp_format(self):
        """Line 683: shp format -> actual_fmt from suffix."""
        ex = make_executor()
        result_node = make_node("result", "result", fileName="output", format="shp")
        df = make_df(3)

        saved_path = Path("/tmp/output.zip")

        with patch("app.core.manipola.executor.detect_geometry_column", return_value=None), \
             patch.object(ex, "_estrai_srid_da_canvas", return_value=None), \
             patch("app.core.manipola.executor.get_temp_output_path",
                   return_value=Path("/tmp/output.shp")), \
             patch("app.core.manipola.executor.save_dataframe", return_value=saved_path), \
             patch("app.core.manipola.executor.save_result_blob"):
            ex.project_service.get_project_metadata.return_value = None
            result = ex._salva_risultato("proj-1", result_node, df)

        assert result["success"] is True
        assert result["format"] == "zip"

    def test_salva_no_canvas(self):
        """Lines 665-668: canvas=None -> default CRS EPSG:4326."""
        ex = make_executor()
        result_node = make_node("result", "result", fileName="output", format="csv")
        df = make_df(2)

        saved_path = Path("/tmp/output.csv")

        with patch("app.core.manipola.executor.detect_geometry_column", return_value=None), \
             patch("app.core.manipola.executor.get_temp_output_path", return_value=saved_path), \
             patch("app.core.manipola.executor.save_dataframe", return_value=saved_path) as mock_save, \
             patch("app.core.manipola.executor.save_result_blob"):
            ex.project_service.get_project_metadata.return_value = None
            result = ex._salva_risultato("proj-1", result_node, df, canvas=None)

        assert result["success"] is True
        # CRS should be default EPSG:4326
        call_args = mock_save.call_args[0]
        assert call_args[4] == "EPSG:4326"

    def test_salva_metadata_exception_non_blocking(self):
        """Lines 678-679: get_project_metadata exception -> group_id stays None."""
        ex = make_executor()
        result_node = make_node("result", "result", fileName="output", format="csv")
        df = make_df(2)

        saved_path = Path("/tmp/output.csv")

        with patch("app.core.manipola.executor.detect_geometry_column", return_value=None), \
             patch.object(ex, "_estrai_srid_da_canvas", return_value=None), \
             patch("app.core.manipola.executor.get_temp_output_path", return_value=saved_path), \
             patch("app.core.manipola.executor.save_dataframe", return_value=saved_path), \
             patch("app.core.manipola.executor.save_result_blob") as mock_blob:
            ex.project_service.get_project_metadata.side_effect = RuntimeError("db error")
            result = ex._salva_risultato("proj-1", result_node, df, make_canvas([]))

        assert result["success"] is True
        # group_id should be None since metadata lookup failed
        blob_call = mock_blob.call_args
        assert blob_call[1].get("group_id") is None or blob_call[0][-1] is None


# =============================================================================
# _estrai_srid_da_canvas — line 698 (node without columns -> continue)
# =============================================================================

class TestEstraiSridDaCanvasExtended:

    def test_node_without_columns_skipped(self):
        """Line 698: node with no columns -> continue, does not crash."""
        ex = make_executor()
        node = make_node("t1", "transform")  # no columns
        node2 = make_node("s1", "file_source",
                          columns=[{"name": "geom", "type": "geometry:point:4326"}])
        canvas = make_canvas([node, node2])
        srid = ex._estrai_srid_da_canvas(canvas)
        assert srid == 4326

    def test_node_with_data_columns(self):
        """Line 696: columns from node.data (backward compat)."""
        ex = make_executor()
        node = make_node("s1", "file_source")
        node.columns = []
        node.data = {"columns": [{"name": "geom", "type": "geometry:polygon:32632"}]}
        canvas = make_canvas([node])
        srid = ex._estrai_srid_da_canvas(canvas)
        assert srid == 32632


# =============================================================================
# _costruisci_colonne_con_tipi — lines 718-719, 751
# =============================================================================

class TestCostruisciColonneConTipiExtended:

    def test_datetime_dtype_detected(self):
        """Line 758: datetime dtype -> 'date'."""
        ex = make_executor()
        df = pd.DataFrame({"ts": pd.to_datetime(["2024-01-01", "2024-06-15"])})
        result = ex._costruisci_colonne_con_tipi(df, {})
        assert result[0]["type"] == "date"

    def test_geometry_dtype_detected(self):
        """Line 759: GeoDataFrame geometry dtype -> 'geometry'."""
        gpd = pytest.importorskip("geopandas")
        from shapely.geometry import Point
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2]}, geometry=[Point(0, 0), Point(1, 1)], crs="EPSG:4326"
        )
        ex = make_executor()
        result = ex._costruisci_colonne_con_tipi(gdf, {})
        types = {c["name"]: c["type"] for c in result}
        assert types["geometry"] == "geometry"

    def test_str_type_geometry_name_with_wkt_content(self):
        """Line 751: canvas type is 'str' but col name is 'geometry' with WKT -> 'geometry'."""
        ex = make_executor()
        df = pd.DataFrame({"geometry": ["POINT(0 0)", "POLYGON((0 0,1 0,1 1,0 0))"]})
        canvas_map = {"geometry": "str"}
        result = ex._costruisci_colonne_con_tipi(df, canvas_map)
        assert result[0]["type"] == "geometry"

    def test_unknown_dtype_string_fallback(self):
        """Line 760: object dtype, not geometry -> 'str'."""
        ex = make_executor()
        df = pd.DataFrame({"note": ["hello", "world"]})
        result = ex._costruisci_colonne_con_tipi(df, {})
        assert result[0]["type"] == "str"
