"""A3 regressions through real preparation, inference and execution boundaries."""
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from app.core.projects.canvas_models import CanvasState, Edge, Node
from app.core.projects.schema_inference import SchemaInferenceService
from app.core.projects.canvas_validation import validate_canvas

pytestmark = [pytest.mark.unit, pytest.mark.contract]


def node(kind, identity, **values):
    return Node.model_validate({"id": identity, "type": kind, "position": {"x": 0, "y": 0}, **values})


def chain(*nodes):
    return CanvasState(nodes=list(nodes), edges=[Edge(id=f"e{i}", source=a.id, target=b.id)
                                               for i, (a, b) in enumerate(zip(nodes, nodes[1:]))])


@pytest.mark.parametrize("provider", ["sqlite", "postgres"])
def test_sql_validation_uses_execution_connection_options(monkeypatch, provider):
    from app.core.projects import schema_execution_sources
    from app.core.estrai.sql_preparation import sql_connection_options
    from app.core.estrai.query_generator import QueryGenerator
    canvas = chain(node("source", "s", table_name="data", columns=[{"name": "a", "type": "int"}]),
                   node("result", "r"))
    connection = SimpleNamespace(type=provider, db_schema="public" if provider == "postgres" else None)
    options = sql_connection_options(connection)
    monkeypatch.setattr(schema_execution_sources, "load_sql_options", lambda cid: options if cid == "resolved" else None)
    original = QueryGenerator.generate_sql
    seen = []
    def generate(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        seen.append((kwargs, result))
        return result
    monkeypatch.setattr(QueryGenerator, "generate_sql", generate)
    report = validate_canvas(canvas, project_type="estrai", execution_context={"default_connection_id": "resolved"})
    assert not any(i.code.startswith("sql.") for i in report.issues)
    assert seen[0][0] == options
    assert seen[0][1] == original(QueryGenerator(), canvas.nodes, canvas.edges, "r", **options)


def test_unreadable_sql_connection_is_not_guaranteed(monkeypatch):
    from app.core.projects import schema_execution_sources
    monkeypatch.setattr(schema_execution_sources, "load_sql_options", Mock(side_effect=ValueError("not available")))
    report = validate_canvas(chain(node("source", "s", table_name="data"), node("result", "r")),
                             project_type="estrai", execution_context={"default_connection_id": "db"})
    assert [(i.code, i.severity) for i in report.issues if i.code.startswith("sql.")] == [("sql.not_guaranteed", "warning")]


def test_rename_96_columns_through_schemas_save_and_contract(monkeypatch):
    from app.core.manipola import executor as module
    from app.core.projects import router
    from app.core.dataframes.frame_io import save_dataframe_to_bytes
    from app.shared.contracts.auth import CurrentUser
    names = ["name", "city_id"] + [f"field_{i}" for i in range(94)]
    source = node("staticValues", "s", data={"fields": [
        {"name": name, "value": "Roma" if name == "name" else "1", "field_type": "text"} for name in names]})
    canvas = chain(source,
                   node("transform", "sort", transform_op="sort", config={"columns": ["city_id"]}),
                   node("transform", "distinct", transform_op="distinct", config={"subset": ["city_id"]}),
                   node("transform", "rename", transform_op="rename", config={"renames": [
                       {"old_name": key, "new_name": "Comune"} for key in ["name", "city_id"]]}),
                   node("result", "r", format="csv"))
    captured = {}
    service = SimpleNamespace(get_project_canvas=lambda _: canvas,
        get_project_metadata=lambda _: SimpleNamespace(group_id="g", project_type="manipola"),
        get_project_output_contract=lambda _: None,
        update_project_output_contract=lambda _, value: captured.update(contract=value))
    def persist(**kwargs):
        captured["frame"] = kwargs["df"].copy()
        captured["bytes"] = save_dataframe_to_bytes(kwargs["df"], "csv")
        return SimpleNamespace(output_filename="out.csv", result_id="id", output_columns=list(kwargs["df"].columns))
    monkeypatch.setattr(module, "save_pipeline_result_outcome", persist)
    monkeypatch.setattr(module, "append_step", lambda *args, **kwargs: None)
    monkeypatch.setattr(router, "_project_service", service)
    monkeypatch.setattr(router, "_schema_service", SchemaInferenceService(service))
    monkeypatch.setattr(router, "assert_group_access", lambda *args: None)
    monkeypatch.setattr(router, "_prepare_canvas_for_owner", lambda _, canvas, *args, **kwargs: canvas)
    schemas = router.infer_project_schemas("p", router.SchemaInferenceRequest(canvas=canvas.model_dump(exclude_unset=True)),
        CurrentUser(user_id="u", username="u", role="admin", group_id="g"))
    result = module.ManipolaExecutor(project_service=service).execute(
        "p", canvas, execution_context={"group_id": "g"}, intermediate_mode_override="none")
    assert result["success"], result
    saved = pd.read_csv(BytesIO(captured["bytes"]), sep=None, engine="python")
    predicted = [col["name"] for col in schemas["schemas"]["r"]]
    assert predicted == list(saved.columns) == list(captured["frame"].columns)
    assert predicted == [col["physical_name"] for col in captured["contract"]["columns"]]
    assert len(predicted) == 96 and predicted[:2] == ["Comune", "Comune_2"]


def test_arricchisci_output_uses_inferred_identity_after_upstream_rename(monkeypatch):
    from app.core.arricchisci.canvas_orchestrator import run_enrichment_canvas
    from app.shared.execution.canvas_graph import CanvasGraph
    from app.core.projects.canvas_schema_preparation import infer_canvas_columns
    source = node("file_source", "s", columns=[{"id": "stable-id", "name": "old_name", "type": "str"},
                                                {"id": "keep-id", "name": "keep", "type": "str"}])
    canvas = chain(source, node("result", "r"))
    schema = infer_canvas_columns(canvas, project_type="arricchisci")["s"]
    identity = next(c["id"] for c in schema if c["name"] == "old_name")
    source.columns[0].alias = "new_name"
    canvas.nodes[-1].data = {"output_columns": [{"name": "old_name", "name_column_id": identity, "keep": False}],
                             "inputColumns": [{"id": identity, "name": "old_name"}]}
    prediction = infer_canvas_columns(canvas, project_type="arricchisci")["r"]
    svc = SimpleNamespace(_load_file_source=lambda *args, **kwargs: pd.DataFrame({"new_name": ["Rome"], "keep": ["yes"]}))
    result = run_enrichment_canvas(svc, "p", graph=CanvasGraph.from_canvas(canvas), ctx=object(),
                                  api_connections={}, execution_order=["s", "r"], groups=[("single", ["s"]), ("single", ["r"])])
    assert [c["name"] for c in prediction] == list(result["r"].columns) == ["keep"]


@pytest.mark.parametrize("stale", [False, True])
def test_geo_output_uses_inferred_transform_columns(stale):
    from app.core.manipola.canvas_schema_runtime import build_canvas_type_map
    source = node("staticValues", "s", data={"fields": [{"name": "lat", "value": "45", "field_type": "number"},
                                                          {"name": "lon", "value": "9", "field_type": "number"}]})
    transform = node("transform", "t", transform_op="geo_from_coords", config={"lat_column": "lat", "lon_column": "lon"},
                     columns=[{"name": "geometry", "type": "str"}] if stale else [])
    canvas = chain(source, transform, node("result", "r", data={"format": "geojson"}))
    report = validate_canvas(canvas, project_type="manipola")
    assert not any(i.code == "RESULT_GEO_NO_GEOMETRY" for i in report.issues)
    assert build_canvas_type_map(canvas)["geometry"].startswith("geometry")


def test_api_geometry_overrides_absent_and_stale_saved_columns(monkeypatch):
    from app.core.projects import schema_execution_sources
    from app.core.manipola.canvas_schema_runtime import build_canvas_type_map
    from app.core.estrai.executor import EstraiExecutor
    api = {"output_mapping": [{"path": "shape", "column": "geometry", "type": "geometry"}]}
    from app.core.arricchisci.api_connector import api_connection_service
    monkeypatch.setattr(api_connection_service, "get_api_connection", lambda *args, **kwargs: api)
    monkeypatch.setattr(schema_execution_sources, "load_api_schema_config", lambda _: api)
    source = node("staticValues", "s", fields=[{"name": "a", "value": "1", "field_type": "integer"}])
    canvas = chain(source, node("api_enrich", "a", api_id="api"), node("result", "r", data={"format": "geojson"}))
    maps = []
    executor = EstraiExecutor(None, None, None, None)
    for columns in ([], [{"name": "geometry", "type": "str"}], [{"name": "geometry", "type": "geometry"}]):
        canvas.nodes[1] = Node.model_validate({**canvas.nodes[1].model_dump(exclude_unset=True), "columns": columns})
        report = validate_canvas(canvas, project_type="arricchisci")
        assert not any(i.code == "RESULT_GEO_NO_GEOMETRY" for i in report.issues)
        maps.append((build_canvas_type_map(canvas), executor._build_canvas_type_map(canvas)))
    assert maps[0] == maps[1] == maps[2]
    assert maps[0][0]["geometry"] == maps[0][1]["geometry"] == "geometry"


def test_geometry_file_identical_with_and_without_derived_columns(monkeypatch):
    from app.core.manipola import executor as module
    from app.core.dataframes.frame_io import save_dataframe_to_bytes
    import json
    source = node("staticValues", "s", fields=[{"name": "lat", "value": "45", "field_type": "number"},
                                               {"name": "lon", "value": "9", "field_type": "number"}])
    canvas = chain(source, node("transform", "t", transform_op="geo_from_coords",
                               config={"lat_column": "lat", "lon_column": "lon"}),
                   node("result", "r", format="geojson"))
    captured = []
    service = SimpleNamespace(get_project_canvas=lambda _: canvas,
        get_project_metadata=lambda _: SimpleNamespace(group_id="g", project_type="manipola"),
        get_project_output_contract=lambda _: None, update_project_output_contract=lambda *args: None)
    def persist(**kwargs):
        payload = save_dataframe_to_bytes(kwargs["df"], "geojson", geom_column=kwargs["geom_column"],
                                          crs=kwargs["crs"], column_types=kwargs["column_types"])
        contents = json.loads(payload)
        contents.pop("name", None)  # GDAL assigns a fresh temporary layer UUID.
        captured.append((kwargs["column_types"], contents))
        return SimpleNamespace(output_filename="out.geojson", result_id="id", output_columns=list(kwargs["df"].columns))
    monkeypatch.setattr(module, "save_pipeline_result_outcome", persist)
    monkeypatch.setattr(module, "append_step", lambda *args, **kwargs: None)
    for columns in ([], [{"name": "geometry", "type": "geometry:point"}], [{"name": "geometry", "type": "str"}]):
        canvas.nodes[1] = Node.model_validate({**canvas.nodes[1].model_dump(exclude_unset=True), "columns": columns})
        result = module.ManipolaExecutor(service).execute("p", canvas, execution_context={"group_id": "g"}, intermediate_mode_override="none")
        assert result["success"], result
    assert captured[0] == captured[1] == captured[2]
    assert captured[0][1]["features"][0]["geometry"]["type"] == "Point"


def test_preparation_constraint_messages_have_one_owner():
    import ast
    import inspect
    from app.core.manipola.transforms import filtering
    tree = ast.parse(inspect.getsource(filtering))
    messages = ["Filter: in/not_in richiede", "Filter: valori non numerici"]
    for prefix in messages:
        owners = [fn.name for fn in tree.body if isinstance(fn, ast.FunctionDef)
                  if any(isinstance(item, ast.Constant) and isinstance(item.value, str) and item.value.startswith(prefix)
                         for item in ast.walk(fn))]
        assert owners == ["prepare_filter"]


def test_batch_and_standalone_estimate_use_native_preparation():
    from app.core.arricchisci.canvas_preflight import estimate_api_input
    from app.core.arricchisci.enrichment_preflight import build_estimate_response
    frame = pd.DataFrame({"a": [1, 1, 2, None, 3]})
    api = {"batch": {"enabled": True, "max_size": 2}, "parameters": [{"name": "q", "required": True}]}
    data = {"param_mapping": {"q": "a"}}
    estimate = estimate_api_input(data, api, frame)
    assert estimate["api_calls"] == 2
    # Percorso legacy (senza node_id/canvas): risposta esatta di HEAD 3c8609fe,
    # stesse sei chiavi e conteggio delle combinazioni uniche dell'executor.
    from app.core.arricchisci.enrichment_service import EnrichmentExecutor
    from app.core.arricchisci.models import EnrichmentOptions
    standalone = build_estimate_response(SimpleNamespace(**data), api, frame, executor_cls=EnrichmentExecutor,
                                         options_cls=EnrichmentOptions, format_time=str)
    assert set(standalone) == {"total_rows", "unique_combinations", "estimated_api_calls", "estimated_time_seconds",
                               "estimated_time_formatted", "deduplication_savings_percent"}
    assert standalone["total_rows"] == 5


@pytest.mark.parametrize("pagination", [{}, {"type": "cursor", "cursor_path": "next"}])
def test_generator_quantity_and_real_preparation(pagination):
    from app.core.arricchisci.canvas_preflight import estimate_api_input
    api = {"api_type": "generator", "endpoint": {"url": "https://example.org", "method": "GET"}, "pagination": pagination}
    result = estimate_api_input({}, api, None)
    assert result["initial_api_calls"] == 1
    assert result["api_calls"] == (None if pagination else 1)
    assert result["status"] == ("unknown" if pagination else "estimated")


@pytest.mark.parametrize("invalid", [
    {"endpoint": {}},
    {"async": {"enabled": True}, "pagination": {"type": "cursor"}},
    {"pagination": {"type": "offset", "page_size": "bad"}},
])
def test_generator_preflight_and_execution_reject_same_configuration(invalid):
    from app.core.arricchisci.canvas_preflight import estimate_api_input
    from app.core.arricchisci.api_connector import api_connection_service
    api = {"api_type": "generator", "endpoint": {"url": "https://example.org", "method": "GET"}, **invalid}
    with pytest.raises(ValueError) as runtime:
        api_connection_service._execute_api_generator(api)
    with pytest.raises(ValueError) as preflight:
        estimate_api_input({}, api, None)
    assert str(runtime.value) == str(preflight.value)


def test_rest_file_preflight_uses_real_parser(monkeypatch):
    from app.core.arricchisci.canvas_preflight import estimate_api_input
    from app.core.arricchisci.api_connector import api_connection_service
    monkeypatch.setattr(api_connection_service, "_validate_outbound_url", lambda _: None)
    api = {"connection_type": "rest_file", "endpoint": {"url": "https://example.org", "method": "POST"}}
    assert estimate_api_input({}, api, None)["api_calls"] == 1
    api["endpoint"]["method"] = "GET"
    _, error = api_connection_service._parse_rest_file_config(api)
    with pytest.raises(ValueError) as failure:
        estimate_api_input({}, api, None)
    assert str(failure.value) == error["error"]


def test_arricchisci_complete_execution_through_save_and_contract(monkeypatch):
    from app.core.arricchisci import executor as module
    from app.core.arricchisci import api_connection_support, api_connection_generator_enricher
    from app.core.arricchisci.api_connector import api_connection_service
    from app.core.projects import schema_execution_sources
    from app.core.dataframes.frame_io import save_dataframe_to_bytes
    api = {"api_type": "generator", "endpoint": {"url": "https://example.org", "method": "GET"},
           "output_mapping": [{"path": "value", "column": "value", "type": "int"}]}
    canvas = chain(node("api_enrich", "a", api_id="api"), node("result", "r", format="csv"))
    captured = {}
    service = SimpleNamespace(get_project_canvas=lambda _: canvas,
        get_project_metadata=lambda _: SimpleNamespace(group_id="g", project_type="arricchisci"),
        get_project_output_contract=lambda _: None,
        update_project_output_contract=lambda _, value: captured.update(contract=value))
    def persist(**kwargs):
        captured["frame"] = kwargs["df"].copy()
        captured["bytes"] = save_dataframe_to_bytes(kwargs["df"], "csv")
        return SimpleNamespace(output_filename="out.csv", result_id="saved", output_columns=list(kwargs["df"].columns))
    monkeypatch.setattr(api_connection_service, "list_api_connections_decrypted", lambda: {"api": api})
    monkeypatch.setattr(schema_execution_sources, "load_api_schema_config", lambda _: api)
    monkeypatch.setattr(api_connection_generator_enricher, "execute_http_request", lambda *args, **kwargs:
                        SimpleNamespace(status_code=200, headers={"Content-Type": "application/json"}, json=lambda: [{"value": 2}, {"value": 3}]))
    monkeypatch.setattr(api_connection_support, "save_pipeline_result_outcome", persist)
    monkeypatch.setattr(module, "append_step", lambda *args, **kwargs: None)
    result = module.EnrichExecutor(service).execute("p", canvas, execution_context={"group_id": "g"}, intermediate_mode_override="none")
    assert result["success"], result
    assert captured["frame"].to_dict("list") == {"value": [2, 3]}
    assert [c["physical_name"] for c in captured["contract"]["columns"]] == ["value"]
    assert pd.read_csv(BytesIO(captured["bytes"]), sep=None, engine="python").shape[0] == 2
