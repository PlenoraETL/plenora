from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.core.manipola.executor import ManipolaExecutor
from app.core.projects.canvas_models import CanvasState, Edge, Node, Position


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _source_node(node_id: str) -> Node:
    return Node(
        id=node_id,
        type="staticValues",
        position=Position(x=0, y=0),
        fields=[{"name": "id", "type": "int", "value": "1"}],
    )


def _transform_node(node_id: str) -> Node:
    return Node(
        id=node_id,
        type="transform",
        position=Position(x=100, y=0),
        transform_op="rename",
        data={},
    )


def _result_node(node_id: str = "result") -> Node:
    return Node(
        id=node_id,
        type="result",
        position=Position(x=200, y=0),
        data={},
    )


def _edge(edge_id: str, source: str, target: str) -> Edge:
    return Edge(id=edge_id, source=source, target=target)


def test_applica_trasformazioni_releases_linear_intermediate_dataframes():
    executor = ManipolaExecutor(project_service=MagicMock())
    canvas = CanvasState(
        nodes=[_source_node("src"), _transform_node("t1"), _transform_node("t2")],
        edges=[
            _edge("e1", "src", "t1"),
            _edge("e2", "t1", "t2"),
        ],
    )

    dataframes = {"src": pd.DataFrame({"id": [1, 2, 3]})}
    t1_df = pd.DataFrame({"id": [1, 2, 3], "a": [10, 20, 30]})
    t2_df = pd.DataFrame({"id": [1, 2, 3], "b": [100, 200, 300]})

    with patch.object(
        ManipolaExecutor,
        "_esegui_trasformazione",
        side_effect=[t1_df, t2_df],
    ):
        result = executor._applica_trasformazioni(
            canvas=canvas,
            dataframes=dataframes,
            run_id=None,
            project_id=None,
        )

    assert result is t2_df
    assert "src" not in dataframes
    assert "t1" not in dataframes
    assert dataframes["t2"] is t2_df


def test_applica_trasformazioni_keeps_branch_source_until_last_consumer():
    executor = ManipolaExecutor(project_service=MagicMock())
    canvas = CanvasState(
        nodes=[
            _source_node("src"),
            _transform_node("left"),
            _transform_node("right"),
        ],
        edges=[
            _edge("e1", "src", "left"),
            _edge("e2", "src", "right"),
        ],
    )

    source_df = pd.DataFrame({"id": [1, 2, 3]})
    dataframes = {"src": source_df}
    left_df = pd.DataFrame({"id": [1], "branch": ["left"]})
    right_df = pd.DataFrame({"id": [2], "branch": ["right"]})
    seen_inputs = []

    def _execute(_self, _node, inputs):
        seen_inputs.append(inputs[0])
        return left_df if len(seen_inputs) == 1 else right_df

    with patch.object(
        ManipolaExecutor,
        "_esegui_trasformazione",
        autospec=True,
        side_effect=_execute,
    ):
        result = executor._applica_trasformazioni(
            canvas=canvas,
            dataframes=dataframes,
            run_id=None,
            project_id=None,
        )

    assert result is right_df
    assert seen_inputs == [source_df, source_df]
    assert "src" not in dataframes
    assert dataframes["left"] is left_df
    assert dataframes["right"] is right_df


def test_execute_clears_frame_map_before_group_by_and_save():
    executor = ManipolaExecutor(project_service=MagicMock())
    canvas = CanvasState(
        nodes=[_source_node("src"), _result_node()],
        edges=[_edge("e1", "src", "result")],
    )
    final_df = pd.DataFrame({"id": [1, 2, 3]})
    captured = {}

    def _load(_canvas, dataframes, *_args, **_kwargs):
        captured["dataframes"] = dataframes
        dataframes["src"] = final_df
        return None

    def _transform(_canvas, dataframes, **_kwargs):
        dataframes["dead_terminal"] = pd.DataFrame({"unused": [1]})
        return final_df

    def _group_by(df, _result):
        assert df is final_df
        assert captured["dataframes"] == {}
        return df

    with patch.object(
        executor, "_init_security_context", return_value="group-1"
    ), patch.object(
        executor, "_carica_sorgenti_progetto", side_effect=_load
    ), patch.object(
        executor, "_carica_sorgenti_file", return_value=None
    ), patch.object(
        executor, "_carica_sorgenti_datasource", return_value=None
    ), patch.object(
        executor, "_carica_sorgenti_statiche", return_value=None
    ), patch.object(
        executor, "_applica_trasformazioni", side_effect=_transform
    ), patch.object(
        executor, "_applica_group_by", side_effect=_group_by
    ), patch.object(
        executor,
        "_salva_risultato",
        return_value={"success": False, "message": "test stop"},
    ):
        executor.execute("project-1", canvas)

    assert captured["dataframes"] == {}


def test_preview_clears_frame_map_before_group_by():
    executor = ManipolaExecutor(project_service=MagicMock())
    canvas = CanvasState(
        nodes=[_source_node("src"), _result_node()],
        edges=[_edge("e1", "src", "result")],
    )
    final_df = pd.DataFrame({"id": [1, 2, 3]})
    captured = {}

    def _load(_canvas, dataframes, *_args, **_kwargs):
        captured["dataframes"] = dataframes
        dataframes["src"] = final_df
        return None

    def _transform(_canvas, dataframes, **_kwargs):
        dataframes["dead_terminal"] = pd.DataFrame({"unused": [1]})
        return final_df

    def _group_by(df, _result):
        assert df is final_df
        assert captured["dataframes"] == {}
        return df

    with patch.object(
        executor, "_init_security_context", return_value="group-1"
    ), patch.object(
        executor, "_carica_sorgenti_progetto", side_effect=_load
    ), patch.object(
        executor, "_carica_sorgenti_file", return_value=None
    ), patch.object(
        executor, "_carica_sorgenti_datasource", return_value=None
    ), patch.object(
        executor, "_carica_sorgenti_statiche", return_value=None
    ), patch.object(
        executor, "_applica_trasformazioni", side_effect=_transform
    ), patch.object(
        executor, "_applica_group_by", side_effect=_group_by
    ), patch.object(
        executor, "_costruisci_mappa_tipi_canvas", return_value={}
    ), patch.object(
        executor, "_costruisci_colonne_con_tipi", return_value=[]
    ):
        result = executor.preview("project-1", canvas, limit=10)

    assert result["success"] is True
    assert captured["dataframes"] == {}
