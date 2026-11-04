"""A3: shared preparation, optional pivot columns and bounded type inference."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path

import pandas as pd
import pytest

from app.core.manipola.plugin_executor import get_executor
from app.core.manipola.node_validator import ManipolaNodeValidator
from app.core.projects.canvas_models import Node, CanvasState, Edge
from app.core.projects.schema_inference import SchemaInferenceService
from app.core.projects.canvas_validation import _StaticCanvasService, validate_canvas
from app.core.dataframes.schema import TypeInferrer
from app.shared.execution.schema_types import InferredSchema

pytestmark = [pytest.mark.unit, pytest.mark.contract]


def node(kind="transform", **values):
    return Node.model_validate({"id": "n", "type": kind, "position": {"x": 0, "y": 0}, **values})


INVALID = [
    ("formula", {"formula": "valore_catastale_L / vc_tot_contratto_Romi_L", "new_column": "x"}, [pd.DataFrame({"valore_catastale_L": [1]})]),
    ("formula", {"items": [{"new_column": "x"}]}, [pd.DataFrame({"a": [1]})]),
    ("filter", {"column": "absent"}, [pd.DataFrame({"a": [1]})]),
    ("filter", {"conditions": [{"column": "a", "operator": "invalid"}]}, [pd.DataFrame({"a": [1]})]),
    ("filter", {"column": "a", "operator": "in", "value": 123}, [pd.DataFrame({"a": [1]})]),
    ("filter", {"column": "a", "operator": "in", "value": "abc"}, [pd.DataFrame({"a": [1]})]),
    ("join", {"left_on": "right", "right_on": "right"}, [pd.DataFrame({"left": [1]}), pd.DataFrame({"right": [1]})]),
    ("join", {"left_keys": ["a", "b"], "right_keys": ["a"]}, [pd.DataFrame({"a": [1], "b": [2]})] * 2),
    ("lookup", {"column": "a", "mapping": [{"bad": True}]}, [pd.DataFrame({"a": [1]})]),
]


@pytest.mark.parametrize("operation,config,frames", INVALID)
def test_rejections_have_same_reason(operation, config, frames):
    executor = get_executor()
    with pytest.raises((ValueError, TypeError, KeyError)) as runtime:
        executor.execute_with_params(operation, frames, config)
    typed = [InferredSchema(columns=[{"name": name, "type": "int"} for name in frame.columns]) for frame in frames]
    upstream = InferredSchema(columns=[col for item in typed for col in item.columns])
    issues = ManipolaNodeValidator().validate_node(node(transform_op=operation, config=config), upstream=upstream, inputs=typed)
    assert str(runtime.value) in [issue.message for issue in issues]


def test_output_group_removed_column():
    output = node("result", group_by_enabled=True, group_by_columns=["removed"], aggregations=[{"column": "a", "function": "SUM"}])
    issues = ManipolaNodeValidator().validate_node(output, upstream=InferredSchema(columns=[{"name": "a", "type": "int"}]))
    assert any("GROUP BY" in issue.message for issue in issues)


def test_scalar_manifest_is_compiled_during_validation():
    scalar = node("project_input", input_kind="text", input_key="input_1", label="Input 1", scalar_type="string")
    canvas = CanvasState(nodes=[scalar])
    report = validate_canvas(canvas, project_id="p", project_type="manipola")
    assert any(issue.code == "runtime_inputs.invalid" and "collegato" in issue.message for issue in report.issues)


def test_multi_join_inconsistent_pairs_same_reason():
    from app.core.manipola.executor import ManipolaExecutor
    multi = node("multi_join", left_keys=["a", "b"], right_keys=["a"])
    frames = [pd.DataFrame({"a": [1], "b": [2]})] * 2
    with pytest.raises(ValueError) as error:
        ManipolaExecutor(project_service=None)._esegui_multi_join(multi, frames)
    typed = InferredSchema(columns=[{"name": "a", "type": "int"}, {"name": "b", "type": "int"}])
    issues = ManipolaNodeValidator().validate_node(multi, upstream=typed, inputs=[typed, typed])
    assert [issue.message for issue in issues] == [str(error.value)]


def test_rename_duplicate_keeps_runtime_but_rejects_validation():
    frame = pd.DataFrame({"name": ["Roma"], "city_id": [1]})
    config = {"renames": [{"old_name": name, "new_name": "Comune"} for name in frame.columns]}
    actual = get_executor().execute_with_params("rename", [frame], config)
    assert list(actual.columns) == ["Comune", "Comune"]
    issues = ManipolaNodeValidator().validate_node(node(transform_op="rename", config=config), upstream=InferredSchema(columns=[{"name": c, "type": "str"} for c in frame.columns]))
    assert any(all(name in issue.message for name in ["name", "city_id", "Comune"]) for issue in issues)


def test_pivot_both_branches_have_certain_names():
    source = node("file_source", id="s", columns=[{"name": name, "type": "str"} for name in ["id", "kind", "value"]])
    config = {"index_col": "id", "pivot_col": "kind", "value_col": "value", "mapping": {"A": "amount", "B": "other"}}
    pivot = node(transform_op="pivot", config=config)
    canvas = CanvasState(nodes=[source, pivot], edges=[Edge(id="e", source="s", target="n")])
    schema = SchemaInferenceService(_StaticCanvasService(canvas)).infer_node_schema_typed("p", "n")
    assert schema.names_status != "indeterminate"
    required = {col["name"] for col in schema.columns if not col.get("optional")}
    possible = {col["name"] for col in schema.columns}
    assert required == {"id"}
    for value in ["A", "unmapped"]:
        frame = pd.DataFrame({"id": ["1"], "kind": [value], "value": ["2"]})
        actual = get_executor().execute_with_params("pivot", [frame], config)
        assert required <= set(actual.columns) <= possible


@pytest.mark.parametrize("values,expected", [([None] * 5000 + ["POINT(1 2)"], "geometry"),
                                           ([None] * 5000 + ["2026-11-04"], "date"),
                                           ([None] * 5000, "str"), (["testo"] * 10000, "str")])
def test_type_sample_preserves_long_null_prefix(values, expected):
    assert TypeInferrer.infer_column_type(pd.Series(values, dtype=object)) == expected


def test_type_inference_never_dropna_full_column(monkeypatch):
    original = pd.Series.dropna
    sizes = []
    def drop(series, *args, **kwargs):
        sizes.append(len(series))
        return original(series, *args, **kwargs)
    monkeypatch.setattr(pd.Series, "dropna", drop)
    assert TypeInferrer.infer_column_type(pd.Series(["testo"] * 295782, dtype=object)) == "str"
    assert max(sizes) <= 1024


class _FakeProjectsTable:
    """Una riga ``projects`` in memoria: canvas jsonb (dict) e updated_at."""

    def __init__(self, monkeypatch, canvas):
        import json
        import re
        from contextlib import contextmanager
        from app.core.projects import db
        self.row = {"canvas": canvas, "updated_at": datetime(2026, 11, 4, 0, 0, 0, 123456, tzinfo=timezone.utc)}

        def write(sql, values):
            names = re.findall(r"(\w+) = %s", sql.split(" WHERE ")[0])
            for name, value in zip(names, values):
                self.row[name] = json.loads(value) if name == "canvas" else value
            return SimpleNamespace(rowcount=1)

        class Session:
            def fetchone(inner, sql, params=()):
                return dict(self.row)

            def execute(inner, sql, values=()):
                return write(sql, values)

        @contextmanager
        def unit_of_work():
            yield Session()

        monkeypatch.setattr(db, "unit_of_work", unit_of_work)
        monkeypatch.setattr(db, "_execute", write)
        monkeypatch.setattr(db, "get_project_row", lambda *_a, **_k: SimpleNamespace(**self.row))
        monkeypatch.setattr(db, "get_project_raw_canvas_row", lambda *_a, **_k: SimpleNamespace(id="p", canvas=self.row["canvas"]))
        self.repository = db.ProjectRepository()

    def opened_version(self):
        from app.core.projects.canvas_revision import canvas_version
        return canvas_version(self.row["canvas"])


def test_canvas_version_conflict_is_atomic_and_leaves_canvas_untouched(monkeypatch):
    from app.core.projects.canvas_revision import CanvasRevisionConflict
    table = _FakeProjectsTable(monkeypatch, {"nodes": [], "edges": []})
    with pytest.raises(CanvasRevisionConflict) as error:
        table.repository.update_project_row("p", canvas={"nodes": [{"id": "x"}], "edges": []}, base_canvas_version="c1:altro")
    assert table.row["canvas"] == {"nodes": [], "edges": []}
    assert error.value.to_dict()["canvas_version"] == table.opened_version()
    assert error.value.to_dict()["reason"] == "canvas_version_conflict"


def test_validate_invalidation_and_metadata_between_open_and_save_do_not_conflict(monkeypatch):
    table = _FakeProjectsTable(monkeypatch, {"nodes": [], "edges": []})
    base = table.opened_version()
    # POST /validate, invalidazione a valle e metadati toccano updated_at, non il canvas.
    table.repository.update_project_row("p", canvas_validated=True)
    table.repository.update_project_row("p", canvas_validated=False)
    table.repository.update_project_row("p", name="Nuovo nome")
    assert table.opened_version() == base
    revision = {}
    table.repository.update_project_row("p", canvas={"nodes": [{"id": "a"}], "edges": []}, base_canvas_version=base, revision_out=revision)
    assert table.row["canvas"] == {"nodes": [{"id": "a"}], "edges": []}
    assert revision["canvas_version"] == table.opened_version() != base
    assert revision["updated_at"] == table.row["updated_at"]


def test_two_tabs_second_conflicts_and_token_round_trips(monkeypatch):
    from app.core.projects.canvas_revision import CanvasRevisionConflict
    table = _FakeProjectsTable(monkeypatch, {"nodes": [], "edges": []})
    base = table.opened_version()
    first = {}
    table.repository.update_project_row("p", canvas={"nodes": [{"id": "uno"}], "edges": []}, base_canvas_version=base, revision_out=first)
    with pytest.raises(CanvasRevisionConflict):
        table.repository.update_project_row("p", canvas={"nodes": [{"id": "due"}], "edges": []}, base_canvas_version=base)
    assert table.row["canvas"]["nodes"] == [{"id": "uno"}]
    # Il token restituito, rimandato tale e quale, è la nuova base valida.
    second = {}
    table.repository.update_project_row("p", canvas={"nodes": [{"id": "tre"}], "edges": []}, base_canvas_version=first["canvas_version"], revision_out=second)
    assert table.row["canvas"]["nodes"] == [{"id": "tre"}] and second["canvas_version"] != first["canvas_version"]


def test_canvas_version_depends_only_on_content():
    from app.core.projects.canvas_revision import canvas_version
    assert canvas_version({"b": 1, "a": [1.5, "x"]}) == canvas_version('{"a": [1.5, "x"], "b": 1}')
    assert canvas_version({"a": 1}) != canvas_version({"a": 2})


def test_restore_forwards_base_and_written_revision(monkeypatch):
    from app.core.projects.canvas_service import ProjectCanvasService
    from app.core.projects import db
    base = "c1:base"
    written = "c1:scritto"
    def save(project_id, canvas, **kwargs):
        assert kwargs["base_canvas_version"] == base
        kwargs["revision_out"]["canvas_version"] = written
        return True
    monkeypatch.setattr(db, "get_canvas_version", lambda *args: {"canvas": {"nodes": [], "edges": []}})
    revision = {}
    result = ProjectCanvasService(SimpleNamespace(update_canvas=save)).restore_canvas_version("p", "v", base_canvas_version=base, revision_out=revision)
    assert result is not None and revision == {"canvas_version": written}


def test_preflight_template_and_effective_rows(monkeypatch):
    from app.core.arricchisci.canvas_preflight import estimate_canvas, preparation_fingerprint
    from app.core.manipola.canvas_data_preparation import CanvasDataPreparation
    from app.core.orchestrator.execution_contract import PipelineExecutionContract
    api_node = node("api_enrich", api_id="api", data={"api_id": "api", "param_mapping": {"q": "{x},{y}"}, "execute_if": "run=yes", "skip_null_params": True})
    source = node("file_source", id="s")
    canvas = CanvasState(nodes=[source, api_node], edges=[Edge(id="e", source="s", target="n")])
    contract = PipelineExecutionContract(project_id="p", project_type="arricchisci", group_id="g", canvas=canvas.model_dump())
    frame = pd.DataFrame({"x": [1, 1, None, 2], "y": [2, 2, 2, 3], "run": ["yes", "yes", "yes", "no"]})
    monkeypatch.setattr(CanvasDataPreparation, "input", lambda *args: (frame, False, None))
    api = {"parameters": [{"name": "q", "required": True}], "options": {}}
    result = estimate_canvas(contract, {"api": api})[0]
    assert result["valid"] and result["api_calls"] == 1
    first = preparation_fingerprint(contract, {"api": api})
    assert first != preparation_fingerprint(contract, {"api": {**api, "static_parameters": {"x": 1}}})


def test_static_source_distinct_preparation():
    from app.core.manipola.canvas_data_preparation import CanvasDataPreparation
    canvas = CanvasState(nodes=[node("project_input", id="s", input_kind="file"), node(transform_op="lookup")], edges=[Edge(id="e", source="s", target="n")])
    frame, sampled, reason = CanvasDataPreparation(canvas, "g").input("n")
    assert frame is None and not sampled and "runtime" in reason


def test_manipola_complete_execution_through_save_and_contract(monkeypatch):
    from app.core.manipola import executor as module
    source = node("staticValues", id="s", data={"fields": [{"name": "a", "value": "2", "field_type": "integer"}]})
    formula = node(transform_op="formula", config={"formula": "a * 3", "new_column": "b"})
    output = node("result", id="r", format="csv")
    canvas = CanvasState(nodes=[source, formula, output], edges=[Edge(id="e1", source="s", target="n"), Edge(id="e2", source="n", target="r")])
    captured = {}
    service = SimpleNamespace(get_project_canvas=lambda _: canvas,
                              get_project_metadata=lambda _: SimpleNamespace(group_id="g", project_type="manipola"),
                              get_project_output_contract=lambda _: None,
                              update_project_output_contract=lambda _, contract: captured.update(contract=contract))
    def persist(**kwargs):
        captured["frame"] = kwargs["df"].copy()
        return SimpleNamespace(output_filename="out.csv", result_id="id", output_columns=list(kwargs["df"].columns))
    monkeypatch.setattr(module, "save_pipeline_result_outcome", persist)
    monkeypatch.setattr(module, "append_step", lambda *args, **kwargs: None)
    executor = module.ManipolaExecutor(project_service=service)
    result = executor.execute("p", canvas, execution_context={"group_id": "g"}, intermediate_mode_override="none")
    assert result["success"], result
    assert "contract_update_failed" not in result
    assert captured["frame"].to_dict("list") == {"a": [2], "b": [6]}
    assert [col["physical_name"] for col in captured["contract"]["columns"]] == ["a", "b"]


def test_distinct_http_contract_and_cap(monkeypatch):
    from app.core.projects import router
    from app.core.manipola.canvas_data_preparation import CanvasDataPreparation
    from app.shared.contracts.auth import CurrentUser
    source = node("file_source", id="s", columns=[{"name": "a", "type": "int"}])
    target = node(transform_op="lookup", config={"column": "a"})
    body = router.DistinctRequest(canvas={"nodes": [source, target], "edges": [Edge(id="e", source="s", target="n")]}, column="a", limit=1)
    monkeypatch.setattr(router, "_project_service", SimpleNamespace(get_project_metadata=lambda _: SimpleNamespace(group_id="g")))
    monkeypatch.setattr(router, "assert_group_access", lambda *args: None)
    monkeypatch.setattr(router, "_prepare_canvas_for_owner", lambda _, canvas, *args, **kwargs: canvas)
    monkeypatch.setattr(CanvasDataPreparation, "input", lambda *args: (pd.DataFrame({"a": [1, 2, 1]}), False, None))
    result = router.distinct_node_values("p", "n", body, CurrentUser(user_id="u", username="u", role="admin", group_id="g"))
    assert result["values"] == [{"value": "1", "is_null": False, "count": 2}]
    assert result["truncated"] and result["sampled_rows"] == 3


def test_readonly_file_uses_bytes_without_spool(monkeypatch):
    from app.core.artifacts import source_loader as module
    from app.core.artifacts import SourceRef
    import tempfile
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", Mock(side_effect=AssertionError("temporary file")))
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", Mock(side_effect=AssertionError("spool")))
    monkeypatch.setattr(module, "is_resource_visible", lambda *args: True)
    monkeypatch.setattr(module.file_source_service, "get_file_source", lambda _: SimpleNamespace(format="csv", sheet=None, layer=None, group_id="g"))
    get = Mock(return_value=SimpleNamespace(data=b"a,b\n1,2\n"))
    monkeypatch.setattr(module, "default_registry", lambda: SimpleNamespace(get=get))
    frame, reason = module.SourceLoader().load_in_memory(SourceRef(file_source_id="f"), expected_group_id="g")
    assert reason is None and frame.shape == (1, 2)
    assert get.call_args.kwargs == {"mode": "bytes"}
    assert get.call_args.args[0].expected_group_id == "g"


def test_estrai_complete_execution_through_save_and_contract(monkeypatch):
    from app.core.estrai import executor as module
    from app.core.estrai.query_generator import QueryGenerator
    from app.core.query_execution.query_executor import QueryExecutor
    from app.core.projects import schema_execution_sources
    source = node("source", id="s", table_name="data", columns=[{"name": "a", "type": "int"}])
    output = node("result", id="r", format="json")
    canvas = CanvasState(nodes=[source, output], edges=[Edge(id="e", source="s", target="r")])
    captured = {}
    def persist(**kwargs):
        captured["saved"] = True
        return SimpleNamespace(result_id="saved", storage_key="key", output_path=Path("result.json"))
    query = QueryExecutor(engine_factory=Mock(), result_persistence=SimpleNamespace(persist_dataframe_result=persist))
    query.execute_raw = Mock(return_value=([(2,), (3,)], ["a"]))
    service = SimpleNamespace(get_project_canvas=lambda _: canvas,
        get_project_metadata=lambda _: SimpleNamespace(group_id="g", project_type="estrai", default_connection_id="db"),
        get_project_output_contract=lambda _: None,
        update_project_output_contract=lambda _, value: captured.update(contract=value))
    connection = SimpleNamespace(type="sqlite", db_schema=None)
    executor = module.EstraiExecutor(service, query, QueryGenerator("sqlite"), SimpleNamespace(get_connection=lambda _: connection))
    monkeypatch.setattr(executor, "_assert_connection_visible_for_project", lambda *args: None)
    monkeypatch.setattr(schema_execution_sources, "load_sql_provider", lambda _: "sqlite")
    monkeypatch.setattr(module, "append_step", lambda *args, **kwargs: None)
    result = executor.execute("p", canvas, intermediate_mode_override="none")
    assert result["success"], result
    assert captured["saved"] and not result.get("contract_update_failed")
    assert captured["contract"]["columns"][0]["physical_name"] == "a"
