"""Parità con le funzioni reali: nessuna operazione nuova può restare senza casi."""

from types import SimpleNamespace

import pandas as pd
import pytest

from app.core.manipola.plugin_executor import get_executor
from app.core.manipola.canvas_schema_runtime import build_columns_with_types
from app.core.projects.schema_inference import SchemaInferenceService
from app.shared.execution.schema_types import ColumnSchema
from tests2.contracts.inference_runtime_cases import CASES


def test_registry_enumeration():
    assert set(CASES) == set(get_executor().registry)


def frames(op, empty):
    """Ingressi piccoli; i tipi sono ricavati dai dtype effettivi."""
    frame = pd.DataFrame({"id": [1, 1, 2, 2], "a": [1, 2, 3, 4],
                          "b": [1.5, 2.5, 3.5, 4.5], "s": ["ab", "a,b", "ab", "x"]})
    if op.startswith("geo_") or op == "sjoin":
        import geopandas as gpd
        from shapely.geometry import Point
        frame = gpd.GeoDataFrame(frame, geometry=[Point(0, 0), Point(1, 0), Point(1, 1), Point(0, 1)], crs=3857)
    if empty:
        frame = frame.iloc[:0]
    return [frame, frame.copy()]


@pytest.mark.parametrize("op", CASES)
@pytest.mark.parametrize("empty", [False, True], ids=["righe", "vuoto"])
def test_runtime_parity(op, empty):
    inputs = frames(op, empty)
    config = dict(CASES[op])
    for param in get_executor().registry[op].get("parameters", []):
        if "geom" in param["id"] and param["id"] != "keep_geom_type":
            config.setdefault(param["id"], "geometry")
    schemas = [[ColumnSchema(**col, id=f"{side}:{i}", source_name=col["name"])
                for i, col in enumerate(build_columns_with_types(frame, {}))]
               for side, frame in enumerate(inputs)]
    service = SchemaInferenceService(None)
    node = SimpleNamespace(id="t", config=config, data={}, transform_op=op)
    inferred = service._infer_transform_schema(node, schemas, {})
    from tests2.contracts.test_a1_correction import canvas_frames
    completed = infer_canvas(canvas_frames(op, inputs, config), "t")
    identities = [col["id"] for col in completed.columns]
    assert len(identities) == len(set(identities))
    result = get_executor().execute_with_params(op, inputs, config)
    actual = [(col["name"], col["type"]) for col in build_columns_with_types(result, {})]
    status = service._status_cache.get("t", ("certain", ""))[0]
    if not empty:
        predicted_names = [col.name for col in inferred]
        actual_names = [col[0] for col in actual]
        if predicted_names != actual_names:
            # La mancata previsione richiede una prova con lo stesso schema.
            other = [frame.iloc[:0].copy() for frame in inputs]
            alternative = get_executor().execute_with_params(op, other, config)
            assert list(alternative.columns) != actual_names
            assert status == "indeterminate" or any(col.optional for col in inferred)
    types_status = service._types_status_cache.get("t", (status, ""))[0]
    if status == "certain" and types_status == "certain" and not (empty and service._order_status_cache.get("t") == "indeterminate"):
        assert [(col.name, col.type.split("(")[0].split(":")[0]) for col in inferred if not col.optional or col.name in result.columns] == actual



@pytest.mark.parametrize("columns", [[], [{"name": "a", "visible": False}],
                                      [{"name": "a", "alias": "nuova"}]])
def test_source_projection(columns):
    from app.core.dataframes.tabular import filter_visible_columns
    from app.shared.execution.source_column_plan import source_column_plan
    frame = pd.DataFrame({"a": [1]})
    actual = filter_visible_columns(frame, columns)
    plan = source_column_plan(list(frame.columns), columns)
    assert ([name for _, name in plan] if plan else list(frame.columns)) == list(actual.columns)


def infer_frames(op, inputs, config):
    """Inferisce dagli stessi metadati che descrivono i frame della prova."""
    schemas = [[ColumnSchema(**col, id=f"{side}:{i}", source_name=col["name"])
                for i, col in enumerate(build_columns_with_types(frame, {}))]
               for side, frame in enumerate(inputs)]
    service = SchemaInferenceService(None)
    node = SimpleNamespace(id="t", config=config, data={}, transform_op=op)
    from tests2.contracts.test_a1_correction import canvas_frames
    canvas = canvas_frames(op, inputs, config)
    service._schema_cache = {f"s{i}": schema for i, schema in enumerate(schemas)}
    completed = service._infer_node_schema(canvas.nodes[-1], {n.id: n for n in canvas.nodes},
                                          service._group_edges_by_target(canvas.edges), "p")
    identities = [col.id for col in completed]
    assert len(identities) == len(set(identities))
    return completed


@pytest.mark.parametrize("new_join", [False, True])
@pytest.mark.parametrize("left_names,right_names", [
    (["id", "a"], ["key", "b"]),
    (["id", "a", "a"], ["key", "b", "b"]),
    (["id", "id", "a"], ["key", "key", "b"]),
    (["id", "a", "a", "a_1"], ["key", "b"]),
    (["id", "a", "b", "a"], ["key", "c", "d", "c"]),
    (["id", "a"], ["key", "a_L"]),
])
def test_join_duplicates_and_different_keys(new_join, left_names, right_names):
    inputs = [pd.DataFrame([[1] * len(names)], columns=names) for names in (left_names, right_names)]
    config = {"left_keys": ["id"], "right_keys": ["key"]}
    if new_join:
        config["name_conflict"] = "suffix"
    inferred = infer_frames("join", inputs, config)
    result = get_executor().execute_with_params("join", inputs, config)
    assert [col.name for col in inferred] == list(result.columns)
    assert [col.type for col in inferred] == [c["type"] for c in build_columns_with_types(result, {})]


def test_join_key_types():
    inputs = [pd.DataFrame({"id": [1], "a": [2]}), pd.DataFrame({"key": ["1"], "b": [3]})]
    config = {"left_keys": ["id"], "right_keys": ["key"]}
    inferred = infer_frames("join", inputs, config)
    result = get_executor().execute_with_params("join", inputs, config)
    assert [(c.name, c.type) for c in inferred] == [(c["name"], c["type"]) for c in build_columns_with_types(result, {})]


@pytest.mark.parametrize("op", ["concat_columns", "md5_hash", "add_row_number", "geo_buffer", "geo_centroid",
    "geo_area", "geo_convex_hull", "geo_envelope", "geo_point_on_surface", "geo_boundary",
    "geo_length", "geo_perimeter", "geo_vertex_count", "geo_from_coords"])
def test_assignment_overwrites_existing_type(op):
    inputs = frames(op, False)
    config = dict(CASES[op], output_column="s", geom_column="geometry")
    inferred = infer_frames(op, inputs, config)
    actual = get_executor().execute_with_params(op, inputs, config)
    assert [(c.name, c.type.split(":")[0].split("(")[0]) for c in inferred] == [(c["name"], c["type"]) for c in build_columns_with_types(actual, {})]


@pytest.mark.parametrize("op", ["concat_columns", "md5_hash", "mask_data", "text_normalize"])
def test_missing_assignment_sources_are_noop(op):
    frame = pd.DataFrame({"a": [1]})
    config = {"columns": ["assente"], "maskings": [{"column": "assente"}], "overwrite": False}
    inferred = infer_frames(op, [frame], config)
    actual = get_executor().execute_with_params(op, [frame], config)
    assert [(c.name, c.type) for c in inferred] == [(c["name"], c["type"]) for c in build_columns_with_types(actual, {})]


def test_melt_collisions():
    frame = pd.DataFrame({"id": [1], "variable": [2], "value": [3], "value_1": [4]})
    config = {"id_columns": ["id"]}
    inferred = infer_frames("melt", [frame], config)
    actual = get_executor().execute_with_params("melt", [frame], config)
    assert [(c.name, c.type) for c in inferred] == [(c["name"], c["type"]) for c in build_columns_with_types(actual, {})]


@pytest.mark.parametrize("functions", [["avg", "sum"], ["concat", "sum"], ["sum", "sum"]])
def test_multiple_aggregations_aliases(functions):
    frame = pd.DataFrame({"id": [1, 1], "a": [1, 2], "b": [3, 4]})
    config = {"group_by": ["id"], "aggregations": [
        {"column": "a", "function": functions[0], "alias": "prima"},
        {"column": "b", "function": "sum", "alias": "seconda"},
        {"column": "a", "function": functions[1], "alias": "terza"}]}
    inferred = infer_frames("aggregate", [frame], config)
    result = get_executor().execute_with_params("aggregate", [frame], config)
    assert [(c.name, c.type) for c in inferred] == [(c["name"], c["type"]) for c in build_columns_with_types(result, {})]


def test_output_id_after_rename():
    from app.core.projects.canvas_models import CanvasState
    from app.core.manipola.executor import ManipolaExecutor
    from app.core.dataframes.output_columns import apply_output_columns
    from app.shared.execution.canvas_columns import compute_result_columns

    canvas = CanvasState.model_validate({"nodes": [
        {"id": "s", "type": "file_source", "position": {"x": 0, "y": 0}, "data": {"columns": [
            {"id": "stable", "name": "a", "type": "int"}, {"id": "other", "name": "b", "type": "int"}]}},
        {"id": "t", "type": "transform", "position": {"x": 1, "y": 0}, "transform_op": "rename", "config": {"renames": [{"old_name": "a", "new_name": "nuova"}]}},
        {"id": "r", "type": "result", "position": {"x": 2, "y": 0}, "columns": [{"id": "stable", "name": "a", "alias": "etichetta", "visible": False}],
         "output_columns": [{"id": "stable", "name": "a", "label": "etichetta", "keep": True}]}],
        "edges": [{"id": "e1", "source": "s", "target": "t"}, {"id": "e2", "source": "t", "target": "r"}]})
    executor = object.__new__(ManipolaExecutor)
    executor.project_service = None
    result_node = canvas.nodes[-1]
    mapping = executor._result_input_columns(canvas, result_node, "p")
    frame = pd.DataFrame({"b": [2], "nuova": [1]})
    result = apply_output_columns(frame, result_node.output_columns, mapping)
    assert list(result.columns) == ["etichetta", "b"]
    assert result.iloc[0].tolist() == [1, 2]
    hidden = apply_output_columns(frame, [{"id": "stable", "name": "a", "keep": False}], mapping)
    assert list(hidden.columns) == ["b"]
    computed = compute_result_columns(canvas, "r", "p", None, SchemaInferenceService)
    assert computed[0]["id"] == "stable"
    assert computed[0]["alias"] == "etichetta"
    assert computed[0]["visible"] is False


def canvas_of(nodes, edges=()):
    from app.core.projects.canvas_models import CanvasState
    return CanvasState.model_validate({"nodes": [dict(position={"x": 0, "y": i}, **node)
                                                for i, node in enumerate(nodes)], "edges": list(edges)})


def infer_canvas(canvas, target):
    service = SchemaInferenceService(SimpleNamespace(get_project_canvas=lambda _: canvas))
    return service.infer_node_schema_typed("p", target)


@pytest.mark.parametrize("kind", ["file_source", "datasource_node"])
@pytest.mark.parametrize("selection", [[], [{"name": "a", "type": "int", "visible": False}],
    [{"name": "a", "type": "int", "alias": "nuova", "id": "stable"}]])
def test_source_nodes(kind, selection):
    from app.core.dataframes.tabular import filter_visible_columns
    canvas = canvas_of([{"id": "s", "type": kind, "data": {"columns": selection}}])
    typed = infer_canvas(canvas, "s")
    actual = filter_visible_columns(pd.DataFrame({"a": [1]}), selection)
    if selection:
        assert [(c["name"], c["type"]) for c in typed.columns] == [(c["name"], c["type"]) for c in build_columns_with_types(actual, {})]
    else:
        assert typed.status == "indeterminate"


def test_static_node_duplicates_and_casts():
    from app.core.artifacts.source_loader import SourceLoader
    fields = [{"name": "a", "value": "1", "field_type": "integer"},
              {"name": "a", "value": "testo", "field_type": "integer"},
              {"name": "b", "value": "yes", "field_type": "boolean"}]
    canvas = canvas_of([{"id": "s", "type": "staticValues", "data": {"fields": fields}}])
    actual = SourceLoader()._load_static(tuple(fields))
    typed = infer_canvas(canvas, "s")
    assert typed.status == "certain"
    assert [(c["name"], c["type"]) for c in typed.columns] == [(c["name"], c["type"]) for c in build_columns_with_types(actual, {})]


def test_project_source_projection(monkeypatch):
    from app.core.dataframes.tabular import filter_visible_columns, rename_duplicate_columns
    from app.core.projects import schema_inference
    contract = {"columns": [{"id": "a-id", "physical_name": "a", "type": {"logical": "int"}},
                            {"id": "b-id", "physical_name": "b", "type": {"logical": "int"}}]}
    monkeypatch.setattr(schema_inference, "load_project_source_schema_context",
                        lambda _: SimpleNamespace(output_contract=contract))
    selected = [{"name": "a", "alias": "x"}, {"name": "b", "alias": "x"}]
    canvas = canvas_of([{"id": "s", "type": "project_source", "source_project_id": "up", "data": {"columns": selected}}])
    actual, _ = rename_duplicate_columns(filter_visible_columns(pd.DataFrame({"a": [1], "b": [2]}), selected))
    typed = infer_canvas(canvas, "s")
    assert [c["name"] for c in typed.columns] == list(actual.columns)
    assert [c["id"] for c in typed.columns] == ["a-id", "b-id"]


@pytest.mark.parametrize("paired", [False, True])
def test_multi_join_node(paired):
    from app.core.manipola.executor import ManipolaExecutor
    right_key = "key" if paired else "id"
    nodes = [{"id": name, "type": "file_source", "data": {"columns": [
        {"name": key, "type": "int"}, {"name": "a", "type": "int"}]}}
        for name, key in [("s1", "id"), ("s2", right_key)]]
    config = {"join_pairs": [{"left": "id", "right": "key"}]} if paired else {"join_keys": ["id"]}
    nodes.append({"id": "m", "type": "multi_join", "data": {"config": config}})
    canvas = canvas_of(nodes, [{"id": "e1", "source": "s1", "target": "m", "targetHandle": "left"},
                              {"id": "e2", "source": "s2", "target": "m", "targetHandle": "right"}])
    actual = ManipolaExecutor._esegui_multi_join(None, canvas.nodes[-1],
        [pd.DataFrame({"id": [1], "a": [2]}), pd.DataFrame({right_key: [1], "a": [3]})])
    typed = infer_canvas(canvas, "m")
    assert [c["name"] for c in typed.columns] == list(actual.columns)


def test_result_group_alias_collision():
    from app.core.manipola.executor import ManipolaExecutor
    canvas = canvas_of([
        {"id": "s", "type": "file_source", "data": {"columns": [{"name": "id", "type": "int"}, {"name": "a", "type": "int"}]}},
        {"id": "r", "type": "result", "group_by_enabled": True, "group_by_columns": ["id"],
         "aggregations": [{"column": "a", "function": "SUM", "alias": "totale"},
                          {"column": "a", "function": "COUNT", "alias": "totale"}]}],
        [{"id": "e", "source": "s", "target": "r"}])
    actual = ManipolaExecutor._applica_group_by(None, pd.DataFrame({"id": [1, 1], "a": [2, 3]}), canvas.nodes[-1])
    typed = infer_canvas(canvas, "r")
    assert [(c["name"], c["type"]) for c in typed.columns] == [(c["name"], c["type"]) for c in build_columns_with_types(actual, {})]


def test_project_input_file_keeps_extra_columns(monkeypatch):
    from app.core.eseguibili import runtime
    from app.core.eseguibili.models import ExecutableInputDefinition

    captured = []
    monkeypatch.setattr(runtime.file_source_service, "load_dataframe", lambda *a, **k: (pd.DataFrame({"originale": [1], "extra": [True]}), None))

    def capture_upload(**kwargs):
        captured.append(pd.read_csv(kwargs["file_content"]))
        return {"id": "temporary"}

    monkeypatch.setattr(runtime.file_source_service, "create_from_upload", capture_upload)
    monkeypatch.setattr(runtime.eseguibili_db, "mark_temp_file_source_ttl", lambda _: None)
    expected = [{"name": "id", "type": "int"}]
    definition = ExecutableInputDefinition(key="file", label="File", kind="file", expected_columns=expected)
    runtime._materialize_file_input(eseguibile_id="e", definition=definition,
        payload={"file_id": "uploaded", "column_mapping": [{"file_column": "originale", "chain_column": "id"}]}, group_id="g")
    canvas = canvas_of([{"id": "s", "type": "project_input", "input_kind": "file", "expected_columns": expected}])
    typed = infer_canvas(canvas, "s")
    assert list(captured[0].columns) == ["id", "extra"]
    assert typed.status == "indeterminate"
    assert typed.columns[0]["name"] == "id"


def test_unknown_node_and_downstream_are_indeterminate():
    canvas = canvas_of([{"id": "s", "type": "comment"}, {"id": "r", "type": "result"}],
                       [{"id": "e", "source": "s", "target": "r"}])
    canvas.nodes[0].type = "tipo_futuro"
    assert infer_canvas(canvas, "s").names_status == "indeterminate"
    assert infer_canvas(canvas, "r").status == "indeterminate"


def test_join_edge_checks_inputs_and_ids():
    from app.core.projects.canvas_validation import _edge_join_issues
    canvas = canvas_of([
        {"id": "l", "type": "file_source", "data": {"columns": [{"id": "left-id", "name": "id", "type": "int"}]}},
        {"id": "r", "type": "file_source", "data": {"columns": [{"id": "right-id", "name": "key", "type": "int"}]}},
        {"id": "j", "type": "transform", "transform_op": "join", "config": {"left_keys": ["id"], "right_keys": ["key"]}}],
        [{"id": "e1", "source": "l", "target": "j", "targetHandle": "left", "source_column": "left-id", "target_column": "right-id"},
         {"id": "e2", "source": "r", "target": "j", "targetHandle": "right"}])
    assert "key" not in [c["name"] for c in infer_canvas(canvas, "j").columns]
    assert not _edge_join_issues(canvas, canvas.nodes, canvas.edges, {n.id for n in canvas.nodes}, "p", "manipola")


@pytest.mark.parametrize("op,config", [
    ("split_column", {"column": "s"}),
    ("split_column", {"column": "s", "new_columns": ["una", "due", "tre"]}),
    ("pivot", {"index_col": "id", "pivot_col": "s", "value_col": "a", "mapping": {"ab": "presente", "mai": "assente"}}),
    ("transpose", {"output_columns": ["una", "due"]}),
    ("flatten_json", {"column": "s", "output_columns": ["s.a"]}),
    ("statistics", {"column": "a", "group_by": "id", "output_prefix": "a_"}),
])
def test_dynamic_configuration_is_not_a_guarantee(op, config):
    inputs = frames(op, False)
    schema = [[ColumnSchema(**col) for col in build_columns_with_types(frame, {})] for frame in inputs]
    service = SchemaInferenceService(None)
    inferred = service._infer_transform_schema(SimpleNamespace(id="t", config=config, data={}, transform_op=op), schema, {})
    result = get_executor().execute_with_params(op, inputs, config)
    assert isinstance(result, pd.DataFrame)
    assert any(col.optional for col in inferred) or "indeterminate" in (service._status_cache.get("t", ("certain", ""))[0], service._types_status_cache.get("t", ("certain", ""))[0])


def test_stale_projection_preserves_interleaved_duplicates():
    from app.core.dataframes.tabular import filter_visible_columns
    frame = pd.DataFrame([[1, 2, 3]], columns=["a", "b", "a"])
    actual = filter_visible_columns(frame, [{"name": "assente"}])
    pd.testing.assert_frame_equal(actual, frame)


def test_multi_join_three_inputs_suffix_collision():
    from app.core.manipola.executor import ManipolaExecutor
    inputs = [pd.DataFrame({"id": [1], "a": [2], "a_2": [3]}),
              pd.DataFrame({"id": [1], "a": [4]}), pd.DataFrame({"id": [1], "a": [5]})]
    nodes = [{"id": f"s{i}", "type": "file_source", "data": {"columns": build_columns_with_types(frame, {})}}
             for i, frame in enumerate(inputs)]
    nodes.append({"id": "m", "type": "multi_join", "join_keys": ["id"]})
    canvas = canvas_of(nodes, [{"id": f"e{i}", "source": f"s{i}", "target": "m"} for i in range(3)])
    actual = ManipolaExecutor._esegui_multi_join(None, canvas.nodes[-1], inputs)
    typed = infer_canvas(canvas, "m")
    assert [col["name"] for col in typed.columns] == list(actual.columns)
    assert typed.status == "indeterminate"


def test_stale_file_metadata_cannot_be_certain():
    from app.core.dataframes.tabular import filter_visible_columns
    saved = [{"name": "vecchia", "type": "int", "alias": "etichetta"}]
    canvas = canvas_of([{"id": "s", "type": "file_source", "file_source_id": "file", "data": {"columns": saved}}])
    actual = filter_visible_columns(pd.DataFrame({"nuova": ["testo"]}), saved)
    assert list(actual.columns) == ["nuova"]
    assert infer_canvas(canvas, "s").status == "indeterminate"
