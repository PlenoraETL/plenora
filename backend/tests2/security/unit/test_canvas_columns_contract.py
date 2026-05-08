"""
Contract test per compute_result_columns (C9).

Verifica che la funzione:
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from app.shared.execution.canvas_columns import compute_result_columns, find_result_node


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


def _make_canvas(nodes_data, edges_data=None):
    """Costruisce un CanvasState-like object con minimal structure."""
    nodes = [
        SimpleNamespace(
            id=n["id"],
            type=n["type"],
            columns=n.get("columns", []),
            data=n.get("data", {}),
        )
        for n in nodes_data
    ]
    edges = [
        SimpleNamespace(source=e["source"], target=e["target"])
        for e in (edges_data or [])
    ]
    return SimpleNamespace(nodes=nodes, edges=edges)


def _fake_schema(cols):
    """Lista ColumnSchema-like — basta .name/.type per il contract."""
    return [SimpleNamespace(name=c[0], type=c[1]) for c in cols]


def test_find_result_node_returns_result_or_none():
    canvas = _make_canvas([
        {"id": "src1", "type": "source"},
        {"id": "res1", "type": "result"},
    ])
    r = find_result_node(canvas)
    assert r is not None and r.id == "res1"

    canvas_no_result = _make_canvas([
        {"id": "src1", "type": "source"},
        {"id": "trf1", "type": "transform"},
    ])
    assert find_result_node(canvas_no_result) is None


def test_compute_returns_empty_if_result_node_missing():
    canvas = _make_canvas([{"id": "src1", "type": "source"}])
    project_service = MagicMock()

    out = compute_result_columns(
        canvas,
        "nonexistent",
        "proj-1",
        project_service,
        schema_inference_service_factory=MagicMock(),
    )
    assert out == []


def test_compute_delegates_to_schema_inference_and_formats_output():
    canvas = _make_canvas(
        [
            {"id": "src1", "type": "source"},
            {"id": "res1", "type": "result", "columns": []},
        ],
        [{"source": "src1", "target": "res1"}],
    )
    project_service = MagicMock()

    with patch(
        "app.core.projects.schema_inference.SchemaInferenceService"
    ) as MockService:
        instance = MockService.return_value
        instance._schema_cache = {}
        instance._group_edges_by_target.return_value = {"res1": [canvas.edges[0]]}
        instance._infer_node_schema.return_value = _fake_schema(
            [("name", "str"), ("age", "int"), ("lat", "real")]
        )

        out = compute_result_columns(
            canvas,
            "res1",
            "proj-1",
            project_service,
            schema_inference_service_factory=MockService,
        )

        # Forma attesa: dict con name/type/alias/visible
        assert out == [
            {"name": "name", "type": "str", "alias": None, "visible": True},
            {"name": "age", "type": "int", "alias": None, "visible": True},
            {"name": "lat", "type": "real", "alias": None, "visible": True},
        ]
        instance._infer_node_schema.assert_called_once()


def test_compute_preserves_user_alias_and_visible_flags():
    """Se il result_node corrente ha customizzazioni utente (alias,
    visible), devono sopravvivere al recompute."""
    canvas = _make_canvas(
        [
            {"id": "src1", "type": "source"},
            {
                "id": "res1",
                "type": "result",
                "columns": [
                    {"name": "name", "alias": "Nome Utente", "visible": True, "type": "str"},
                    {"name": "age", "alias": None, "visible": False, "type": "int"},
                ],
            },
        ],
        [{"source": "src1", "target": "res1"}],
    )
    project_service = MagicMock()

    with patch(
        "app.core.projects.schema_inference.SchemaInferenceService"
    ) as MockService:
        instance = MockService.return_value
        instance._schema_cache = {}
        instance._group_edges_by_target.return_value = {}
        instance._infer_node_schema.return_value = _fake_schema(
            [("name", "str"), ("age", "int"), ("lat", "real")]  # new col appears
        )

        out = compute_result_columns(
            canvas,
            "res1",
            "proj-1",
            project_service,
            schema_inference_service_factory=MockService,
        )

        by_name = {c["name"]: c for c in out}
        assert by_name["name"]["alias"] == "Nome Utente", "alias non preservato"
        assert by_name["name"]["visible"] is True
        assert by_name["age"]["alias"] is None
        assert by_name["age"]["visible"] is False, "visible=False non preservato"
        # Nuova colonna non customizzata -> defaults
        assert by_name["lat"]["alias"] is None
        assert by_name["lat"]["visible"] is True


def test_compute_handles_inference_exception_gracefully():
    """Se SchemaInferenceService solleva, compute_result_columns logga e
    ritorna [] senza propagare l'eccezione (il chiamante decide cosa fare)."""
    canvas = _make_canvas(
        [{"id": "res1", "type": "result", "columns": []}],
        [],
    )
    project_service = MagicMock()

    with patch(
        "app.core.projects.schema_inference.SchemaInferenceService"
    ) as MockService:
        instance = MockService.return_value
        instance._schema_cache = {}
        instance._group_edges_by_target.return_value = {}
        instance._infer_node_schema.side_effect = RuntimeError("canvas malformato")

        # Non deve sollevare
        out = compute_result_columns(
            canvas,
            "res1",
            "proj-1",
            project_service,
            schema_inference_service_factory=MockService,
        )
        assert out == []


def test_compute_reads_alias_visible_from_data_columns_fallback():
    """Se result_node.columns e' vuota ma data.columns ha valori, li usa come
    fonte delle customizzazioni utente."""
    canvas = _make_canvas(
        [
            {
                "id": "res1",
                "type": "result",
                "columns": [],  # vuoto
                "data": {
                    "columns": [
                        {"name": "x", "alias": "X alias", "visible": False, "type": "str"},
                    ],
                },
            },
        ],
        [],
    )
    project_service = MagicMock()

    with patch(
        "app.core.projects.schema_inference.SchemaInferenceService"
    ) as MockService:
        instance = MockService.return_value
        instance._schema_cache = {}
        instance._group_edges_by_target.return_value = {}
        instance._infer_node_schema.return_value = _fake_schema([("x", "str")])

        out = compute_result_columns(
            canvas,
            "res1",
            "proj-1",
            project_service,
            schema_inference_service_factory=MockService,
        )
        assert out[0]["alias"] == "X alias"
        assert out[0]["visible"] is False
