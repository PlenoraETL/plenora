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
