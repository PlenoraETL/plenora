"""Contratti puri delle nuove selezioni: nessuna dipendenza dataframe."""

import pytest

from app.shared.execution.column_selection import join_column_plan, select_column_plan

pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_selection_order_labels_and_new_upstream_columns():
    assert select_column_plan(["a", "b", "c", "new"], [
        {"name": "c", "label": "C"}, {"name": "a", "keep": False},
    ]) == [("c", "C"), ("b", "b"), ("new", "new")]
    assert select_column_plan(["a"], []) == [("a", "a")]


@pytest.mark.parametrize("columns", [[{"name": "missing"}], [{"name": "a"}, {"name": "a"}], [{"name": "a", "label": "b"}], [{"name": "a", "keep": "false"}]])
def test_selection_invalid_columns(columns):
    with pytest.raises(ValueError, match="Select columns"):
        select_column_plan(["a", "b"], columns)


@pytest.mark.parametrize("mode, expected", [
    ("keep_left", ({"id": "id", "v": "v"}, {"id": "id"})),
    ("keep_right", ({"id": "id"}, {"id": "id", "v": "v"})),
    ("suffix", ({"id": "id", "v": "v_L"}, {"id": "id", "v": "v_R"})),
])
def test_join_conflicts(mode, expected):
    assert join_column_plan(["id", "v"], ["id", "v"], ["id"], ["id"], None, None, mode) == expected


def test_join_empty_selection_keeps_keys():
    assert join_column_plan(["id", "v"], ["rid", "v"], ["id"], ["rid"], [], [], None) == ({"id": "id"}, {"rid": "rid"})


def test_join_key_collides_with_non_key():
    assert join_column_plan(["id", "rid"], ["rid", "id"], ["id"], ["rid"], None, None, "keep_left") == ({"id": "id", "rid": "rid_L"}, {"rid": "rid", "id": "id_R"})


@pytest.mark.parametrize("selection, mode", [(["missing"], None), (None, "invalid")])
def test_join_invalid_selection(selection, mode):
    with pytest.raises(ValueError, match="Join"):
        join_column_plan(["id"], ["id"], ["id"], ["id"], selection, None, mode)


def test_schema_handlers_propagate_names_and_types_without_database():
    from app.core.projects.schema_transform_handlers import TransformSchemaHandlers
    from app.shared.execution.schema_types import ColumnSchema

    handlers = TransformSchemaHandlers(ColumnSchema).build_transform_handlers({})
    left = [ColumnSchema("id", "int"), ColumnSchema("v", "geometry")]
    right = [ColumnSchema("id", "int"), ColumnSchema("v", "str")]
    config = {"left_keys": ["id"], "right_keys": ["id"], "name_conflict": "keep_left"}
    schema = handlers["join"]([left, right], config, "j")
    assert [(c.name, c.type) for c in schema] == [("id", "int"), ("v", "geometry")]
    schema = handlers["select_columns"]([schema], {"columns": [{"name": "v", "label": "shape"}]}, "s")
    assert [(c.name, c.type) for c in schema] == [("shape", "geometry"), ("id", "int")]
    legacy = handlers["formula"]([left], {"new_column": "x", "formula": "id + 1"}, "f")
    assert [(c.name, c.type) for c in legacy][-1] == ("x", "real")
    multiple = handlers["formula"]([left], {"items": [{"new_column": "x", "formula": "id + 1"}, {"new_column": "y", "formula": "x + 1"}]}, "f")
    assert [c.name for c in multiple] == ["id", "v", "x", "y"]
    assert multiple[-1].id != multiple[-2].id
    assert [c.name for c in handlers["formula"]([left], {"items": []}, "f")] == ["id", "v"]


def test_preview_scope_unknown_node_and_ancestors():
    from app.core.projects.canvas_models import CanvasState
    from app.core.projects.canvas_preview_scope import preview_canvas

    canvas = CanvasState.model_validate({
        "nodes": [{"id": name, "type": "staticValues", "position": {"x": 0, "y": 0}} for name in ["a", "b", "c", "other"]],
        "edges": [{"id": "ab", "source": "a", "target": "b"}, {"id": "bc", "source": "b", "target": "c"}],
    })
    assert [n.id for n in preview_canvas(canvas, "b").nodes] == ["a", "b"]
    assert len(canvas.nodes) == 4
    with pytest.raises(ValueError, match="non trovato"):
        preview_canvas(canvas, "missing")


def test_join_rejects_ambiguous_crossed_key_names():
    with pytest.raises(ValueError, match="chiavi omonime"):
        join_column_plan(["a", "b"], ["a", "b"], ["a", "b"], ["b", "a"], None, None, "keep_left")
