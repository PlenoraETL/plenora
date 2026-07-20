from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from app.core.eseguibili import db as eseguibili_db
from app.core.eseguibili import runtime as executable_runtime
from app.core.eseguibili import service as executable_service
from app.core.eseguibili.manifest import compile_manifest
from app.core.eseguibili.models import ExecutableInputDefinition


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _multi_input_canvas() -> dict:
    return {
        "nodes": [
            {
                "id": "customers",
                "type": "project_input",
                "data": {
                    "input_key": "customers_file",
                    "label": "Clienti",
                    "input_kind": "file",
                    "expected_columns": [{"name": "customer_id", "required": True}],
                },
            },
            {
                "id": "orders",
                "type": "project_input",
                "data": {
                    "input_key": "orders_file",
                    "label": "Ordini",
                    "input_kind": "file",
                    "expected_columns": [{"name": "customer_id", "required": True}],
                },
            },
            {
                "id": "country",
                "type": "project_input",
                "data": {
                    "input_key": "country_code",
                    "label": "Paese",
                    "input_kind": "text",
                    "bindings": [
                        {
                            "target_field": "filters.0.value",
                        }
                    ],
                },
            },
            {
                "id": "filter",
                "type": "filter",
                "data": {"filters": [{"column": "country", "value": "IT"}]},
            },
            {"id": "result", "type": "result", "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "customers", "target": "filter"},
            {"id": "e2", "source": "orders", "target": "filter"},
            {"id": "e-country", "source": "country", "target": "filter"},
            {"id": "e3", "source": "filter", "target": "result"},
        ],
    }


def test_manifest_compiles_multiple_files_and_scalar_binding() -> None:
    manifest = compile_manifest(
        source_project_id="project-1",
        project_type="manipola",
        canvas=_multi_input_canvas(),
    )

    assert [item.key for item in manifest.inputs] == [
        "customers_file",
        "orders_file",
        "country_code",
    ]
    assert [(item.input_key, item.mode) for item in manifest.bindings] == [
        ("customers_file", "data_source"),
        ("orders_file", "data_source"),
        ("country_code", "node_field"),
    ]


def _real_manipola_parameter_canvas(target_field: str = "config.value") -> dict:
    return {
        "nodes": [
            {
                "id": "province-input",
                "type": "project_input",
                "position": {"x": 0, "y": 0},
                "input_key": "province",
                "label": "Provincia",
                "input_kind": "text",
                "bindings": [
                    {"target_node_id": "filter-a", "target_field": target_field},
                    {"target_node_id": "filter-b", "target_field": target_field},
                ],
            },
            {
                "id": "filter-a",
                "type": "transform",
                "position": {"x": 300, "y": 0},
                "transform_op": "filter",
                "config": {"column": "province", "operator": "==", "value": "RM"},
            },
            {
                "id": "filter-b",
                "type": "transform",
                "position": {"x": 600, "y": 0},
                "transform_op": "filter",
                "config": {"column": "billing_province", "operator": "==", "value": "RM"},
            },
            {"id": "result", "type": "result", "position": {"x": 900, "y": 0}},
        ],
        "edges": [
            {"id": "flow-1", "source": "filter-a", "target": "filter-b"},
            {"id": "flow-2", "source": "filter-b", "target": "result"},
        ],
    }


def test_real_manipola_parameter_binding_is_generic_and_supports_fanout() -> None:
    canvas = _real_manipola_parameter_canvas(target_field="value")
    manifest = compile_manifest(
        source_project_id="project-1",
        project_type="manipola",
        canvas=canvas,
    )

    assert [binding.target_field for binding in manifest.bindings] == [
        "config.value",
        "config.value",
    ]

    resolved, cleanup_ids, _summary = executable_runtime.resolve_runtime_inputs(
        eseguibile_id="exec-1",
        canvas=canvas,
        manifest=manifest,
        values={"province": "MI"},
        group_id="group-1",
    )

    filters = {
        node["id"]: node
        for node in resolved["nodes"]
        if node.get("type") == "transform"
    }
    assert filters["filter-a"]["config"]["value"] == "MI"
    assert filters["filter-b"]["config"]["value"] == "MI"
    assert "province-input" not in {node["id"] for node in resolved["nodes"]}
    assert cleanup_ids == []


def test_runtime_binding_can_override_an_implicit_registry_default() -> None:
    canvas = _real_manipola_parameter_canvas()
    for node in canvas["nodes"]:
        if node.get("id") == "filter-a":
            node["config"].pop("value")
    province_input = canvas["nodes"][0]
    province_input["bindings"] = [province_input["bindings"][0]]
    manifest = compile_manifest(
        source_project_id="project-1",
        project_type="manipola",
        canvas=canvas,
    )

    resolved, _cleanup_ids, _summary = executable_runtime.resolve_runtime_inputs(
        eseguibile_id="exec-1",
        canvas=canvas,
        manifest=manifest,
        values={"province": "TO"},
        group_id="group-1",
    )

    target = next(node for node in resolved["nodes"] if node["id"] == "filter-a")
    assert target["config"]["value"] == "TO"


def test_runtime_binding_rejects_schema_affecting_transform_parameter() -> None:
    canvas = _real_manipola_parameter_canvas(target_field="config.new_column")
    for node in canvas["nodes"]:
        if node.get("type") == "transform":
            node["transform_op"] = "formula"
            node["config"] = {"new_column": "calculated", "formula": "1"}

    with pytest.raises(ValueError, match="non configurabile"):
        compile_manifest(
            source_project_id="project-1",
            project_type="manipola",
            canvas=canvas,
        )


def test_manifest_rejects_two_inputs_bound_to_the_same_parameter() -> None:
    canvas = _real_manipola_parameter_canvas()
    canvas["nodes"].insert(
        1,
        {
            "id": "second-input",
            "type": "project_input",
            "input_key": "second_province",
            "label": "Seconda provincia",
            "input_kind": "text",
            "bindings": [
                {"target_node_id": "filter-a", "target_field": "config.value"}
            ],
        },
    )

    with pytest.raises(ValueError, match="collegato a piu' input"):
        compile_manifest(
            source_project_id="project-1",
            project_type="manipola",
            canvas=canvas,
        )


def test_manifest_rejects_sensitive_runtime_binding() -> None:
    canvas = _multi_input_canvas()
    scalar = next(node for node in canvas["nodes"] if node["id"] == "country")
    scalar["data"]["bindings"][0]["target_field"] = "connection_id"

    with pytest.raises(ValueError, match="protetto"):
        compile_manifest(
            source_project_id="project-1",
            project_type="manipola",
            canvas=canvas,
        )


@pytest.mark.parametrize("value", [1.5, "NaN", "Infinity", "-Infinity"])
def test_scalar_runtime_rejects_lossy_or_non_finite_numbers(value) -> None:
    kind = "integer" if value == 1.5 else "number"
    definition = ExecutableInputDefinition(
        key="amount",
        label="Importo",
        kind=kind,
    )

    with pytest.raises(ValueError, match="Valore non valido"):
        executable_runtime._coerce_scalar(definition, value)


def test_manifest_rejects_invalid_scalar_regex() -> None:
    canvas = _multi_input_canvas()
    scalar = next(node for node in canvas["nodes"] if node["id"] == "country")
    scalar["data"]["validation"] = {"pattern": "["}

    with pytest.raises(ValueError, match="pattern non valido"):
        compile_manifest(
            source_project_id="project-1",
            project_type="manipola",
            canvas=canvas,
        )


def test_runtime_resolves_two_file_nodes_and_scalar(monkeypatch) -> None:
    manifest = compile_manifest(
        source_project_id="project-1",
        project_type="manipola",
        canvas=_multi_input_canvas(),
    )
    generated = iter(["temp-customers", "temp-orders"])
    monkeypatch.setattr(
        executable_runtime,
        "_materialize_file_input",
        lambda **_kwargs: next(generated),
    )

    canvas, cleanup_ids, summary = executable_runtime.resolve_runtime_inputs(
        eseguibile_id="exec-1",
        canvas=_multi_input_canvas(),
        manifest=manifest,
        values={
            "customers_file": {"file_id": "upload-customers"},
            "orders_file": {"file_id": "upload-orders"},
            "country_code": "DE",
        },
        group_id="group-1",
    )

    nodes = {node["id"]: node for node in canvas["nodes"]}
    assert nodes["customers"]["type"] == "file_source"
    assert nodes["customers"]["data"]["file_source_id"] == "temp-customers"
    assert nodes["orders"]["data"]["file_source_id"] == "temp-orders"
    assert nodes["filter"]["data"]["filters"][0]["value"] == "DE"
    assert "country" not in nodes
    assert all(edge["source"] != "country" for edge in canvas["edges"])
    assert cleanup_ids == ["temp-customers", "temp-orders"]
    assert set(summary) == {"customers_file", "orders_file", "country_code"}


def test_runtime_materializes_multiple_files_as_one_row_union(monkeypatch) -> None:
    canvas = _multi_input_canvas()
    customers = next(node for node in canvas["nodes"] if node["id"] == "customers")
    customers["data"]["multiple"] = True
    manifest = compile_manifest(
        source_project_id="project-1",
        project_type="manipola",
        canvas=canvas,
    )
    definition = next(item for item in manifest.inputs if item.key == "customers_file")
    loaded_groups: list[tuple[str, str]] = []
    output: dict[str, str] = {}

    def load_dataframe(file_id, *, expected_group_id):
        loaded_groups.append((file_id, expected_group_id))
        frames = {
            "upload-a": pd.DataFrame({"id": [1], "name": ["Ada"]}),
            "upload-b": pd.DataFrame({"id": [2], "name": ["Bob"]}),
        }
        return frames[file_id], None

    def create_from_upload(*, file_content, **_kwargs):
        output["csv"] = file_content.read().decode("utf-8")
        return SimpleNamespace(id="temp-union")

    monkeypatch.setattr(executable_runtime.file_source_service, "load_dataframe", load_dataframe)
    monkeypatch.setattr(executable_runtime.file_source_service, "create_from_upload", create_from_upload)
    monkeypatch.setattr(eseguibili_db, "mark_temp_file_source_ttl", lambda *_args: None)

    temp_id = executable_runtime._materialize_file_input(
        eseguibile_id="exec-1",
        definition=definition,
        payload=[
            {
                "file_id": "upload-a",
                "column_mapping": [{"chain_column": "customer_id", "file_column": "id"}],
                "_upload_group_id": "group-a",
            },
            {
                "file_id": "upload-b",
                "column_mapping": [{"chain_column": "customer_id", "file_column": "id"}],
                "_upload_group_id": "group-b",
            },
        ],
        group_id="group-default",
    )

    assert temp_id == "temp-union"
    assert loaded_groups == [("upload-a", "group-a"), ("upload-b", "group-b")]
    assert pd.read_csv(pd.io.common.StringIO(output["csv"])).to_dict("records") == [
        {"customer_id": 1, "name": "Ada"},
        {"customer_id": 2, "name": "Bob"},
    ]


def test_runtime_preserves_geospatial_multi_file_inputs(monkeypatch) -> None:
    import json

    import geopandas as gpd
    from shapely.geometry import Point

    canvas = _multi_input_canvas()
    customers = next(node for node in canvas["nodes"] if node["id"] == "customers")
    customers["data"]["multiple"] = True
    manifest = compile_manifest(
        source_project_id="project-1",
        project_type="manipola",
        canvas=canvas,
    )
    definition = next(item for item in manifest.inputs if item.key == "customers_file")
    output: dict[str, object] = {}

    def load_dataframe(file_id, *, expected_group_id):
        _ = expected_group_id
        offset = 0 if file_id == "geo-a" else 1
        gdf = gpd.GeoDataFrame(
            {"customer_id": [offset + 1]},
            geometry=[Point(offset, offset)],
            crs="EPSG:4326",
        )
        return pd.DataFrame(gdf), gdf

    def create_from_upload(*, file_content, filename, **_kwargs):
        output["filename"] = filename
        output["body"] = json.loads(file_content.read().decode("utf-8"))
        return SimpleNamespace(id="temp-geo-union")

    monkeypatch.setattr(executable_runtime.file_source_service, "load_dataframe", load_dataframe)
    monkeypatch.setattr(executable_runtime.file_source_service, "create_from_upload", create_from_upload)
    monkeypatch.setattr(eseguibili_db, "mark_temp_file_source_ttl", lambda *_args: None)

    executable_runtime._materialize_file_input(
        eseguibile_id="exec-1",
        definition=definition,
        payload=[{"file_id": "geo-a"}, {"file_id": "geo-b"}],
        group_id="group-1",
    )

    assert output["filename"].endswith(".geojson")
    assert len(output["body"]["features"]) == 2
    assert [feature["geometry"]["type"] for feature in output["body"]["features"]] == [
        "Point",
        "Point",
    ]


def test_runtime_cleans_materialized_files_when_later_binding_fails(monkeypatch) -> None:
    canvas = _multi_input_canvas()
    target = next(node for node in canvas["nodes"] if node["id"] == "filter")
    del target["data"]["filters"]
    manifest = compile_manifest(
        source_project_id="project-1",
        project_type="manipola",
        canvas=_multi_input_canvas(),
    )
    generated = iter(["temp-customers", "temp-orders"])
    deleted: list[str] = []
    monkeypatch.setattr(
        executable_runtime,
        "_materialize_file_input",
        lambda **_kwargs: next(generated),
    )
    monkeypatch.setattr(
        executable_runtime.file_source_service,
        "delete_file_source",
        lambda file_id: deleted.append(file_id),
    )

    with pytest.raises(ValueError, match="senza campo binding"):
        executable_runtime.resolve_runtime_inputs(
            eseguibile_id="exec-1",
            canvas=canvas,
            manifest=manifest,
            values={
                "customers_file": {"file_id": "upload-customers"},
                "orders_file": {"file_id": "upload-orders"},
                "country_code": "DE",
            },
            group_id="group-1",
        )

    assert deleted == ["temp-customers", "temp-orders"]


def test_runtime_rejects_binding_to_missing_field(monkeypatch) -> None:
    canvas = _multi_input_canvas()
    manifest = compile_manifest(
        source_project_id="project-1",
        project_type="manipola",
        canvas=canvas,
    )
    target = next(node for node in canvas["nodes"] if node["id"] == "filter")
    del target["data"]["filters"]
    monkeypatch.setattr(
        executable_runtime,
        "_materialize_file_input",
        lambda **kwargs: f"temp-{kwargs['definition'].key}",
    )
    monkeypatch.setattr(
        executable_runtime.file_source_service,
        "delete_file_source",
        lambda _file_id: None,
    )

    with pytest.raises(ValueError, match="senza campo binding"):
        executable_runtime.resolve_runtime_inputs(
            eseguibile_id="exec-1",
            canvas=canvas,
            manifest=manifest,
            values={
                "customers_file": {"file_id": "upload-customers"},
                "orders_file": {"file_id": "upload-orders"},
                "country_code": "DE",
            },
            group_id="group-1",
        )


def test_task_launch_failure_marks_run_and_cleans_inputs(monkeypatch) -> None:
    from app.core.files.service import file_source_service
    from app.core.orchestrator.task_service import task_service

    manifest = {
        "schema_version": 2,
        "project_type": "manipola",
        "source_project_id": "project-1",
        "inputs": [],
        "bindings": [],
    }
    version = eseguibili_db.EseguibileVersionRow(
        id="version-1",
        eseguibile_id="exec-1",
        version_number=1,
        schema_version=2,
        project_type="manipola",
        source_project_id="project-1",
        source_hash="hash",
        canvas={"nodes": [{"id": "out", "type": "result", "data": {}}], "edges": []},
        manifest=manifest,
        status="published",
        created_by="user-1",
        created_at="2026-07-16T00:00:00+00:00",
    )
    executable = eseguibili_db.EseguibileRow(
        id="exec-1",
        name="Executable",
        description="",
        canvas=version.canvas,
        expected_columns=[],
        default_output_format="xlsx",
        group_id="group-1",
        created_by="user-1",
        created_at="2026-07-16T00:00:00+00:00",
        updated_at="2026-07-16T00:00:00+00:00",
        project_type="manipola",
        active_version_id="version-1",
        manifest=manifest,
    )
    completed: list[dict] = []
    deleted: list[str] = []
    monkeypatch.setattr(
        eseguibili_db,
        "get_eseguibile_version",
        lambda *_args, **_kwargs: version,
    )
    monkeypatch.setattr(
        executable_service,
        "resolve_runtime_inputs",
        lambda **_kwargs: (version.canvas, ["temp-input"], {}),
    )
    monkeypatch.setattr(
        eseguibili_db,
        "create_eseguibile_run",
        lambda **_kwargs: "run-1",
    )
    monkeypatch.setattr(
        eseguibili_db,
        "bind_eseguibile_run_task",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        eseguibili_db,
        "complete_eseguibile_run",
        lambda run_id, **kwargs: completed.append({"run_id": run_id, **kwargs}),
    )
    monkeypatch.setattr(
        file_source_service,
        "delete_file_source",
        lambda file_id: deleted.append(file_id),
    )
    monkeypatch.setattr(
        task_service,
        "create_pipeline_task",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("broker down")),
    )

    with pytest.raises(RuntimeError, match="broker down"):
        executable_service.execute_versioned(
            eseguibile=executable,
            raw_inputs={},
            user_id="user-1",
            version_id="version-1",
        )

    assert deleted == ["temp-input"]
    assert completed == [
        {
            "run_id": "run-1",
            "success": False,
            "error_message": "Avvio task fallito: RuntimeError",
        }
    ]


@pytest.mark.parametrize("project_type", ["estrai", "manipola", "arricchisci"])
def test_versioned_execution_dispatches_all_publishable_project_types(
    monkeypatch,
    project_type,
) -> None:
    from app.core.orchestrator.task_service import task_service

    manifest = {
        "schema_version": 2,
        "project_type": project_type,
        "source_project_id": "project-1",
        "inputs": [],
        "bindings": [],
    }
    canvas = {"nodes": [{"id": "out", "type": "result", "data": {}}], "edges": []}
    version = eseguibili_db.EseguibileVersionRow(
        id=f"version-{project_type}",
        eseguibile_id="exec-1",
        version_number=1,
        schema_version=2,
        project_type=project_type,
        source_project_id="project-1",
        source_hash="hash",
        canvas=canvas,
        manifest=manifest,
        status="published",
        created_by="user-1",
        created_at="2026-07-16T00:00:00+00:00",
    )
    executable = eseguibili_db.EseguibileRow(
        id="exec-1",
        name="Executable",
        description="",
        canvas=canvas,
        expected_columns=[],
        default_output_format="xlsx",
        group_id="group-1",
        created_by="user-1",
        created_at="2026-07-16T00:00:00+00:00",
        updated_at="2026-07-16T00:00:00+00:00",
        project_type=project_type,
        active_version_id=version.id,
        manifest=manifest,
    )
    pipeline_calls: list[dict] = []
    enrichment_calls: list[dict] = []
    bound: list[tuple[str, str]] = []
    monkeypatch.setattr(
        eseguibili_db,
        "get_active_eseguibile_version",
        lambda _id: version,
    )
    monkeypatch.setattr(
        eseguibili_db,
        "create_eseguibile_run",
        lambda **_kwargs: "run-1",
    )
    monkeypatch.setattr(
        eseguibili_db,
        "bind_eseguibile_run_task",
        lambda run_id, task_id: bound.append((run_id, task_id)),
    )
    monkeypatch.setattr(
        task_service,
        "create_pipeline_task",
        lambda **kwargs: pipeline_calls.append(kwargs)
        or SimpleNamespace(task_id=kwargs["task_id"]),
    )
    monkeypatch.setattr(
        task_service,
        "create_enrichment_task",
        lambda **kwargs: enrichment_calls.append(kwargs)
        or SimpleNamespace(task_id=kwargs["task_id"]),
    )

    result = executable_service.execute_versioned(
        eseguibile=executable,
        raw_inputs={},
        user_id="user-1",
    )

    if project_type == "arricchisci":
        assert not pipeline_calls
        assert enrichment_calls[0]["execution_context"]["version_id"] == version.id
        assert result.task_id == enrichment_calls[0]["task_id"]
    else:
        assert not enrichment_calls
        contract = pipeline_calls[0]["execution_contract"]
        assert contract["project_type"] == project_type
        assert contract["execution_subject"]["version_id"] == version.id
        assert result.task_id == pipeline_calls[0]["task_id"]
    assert bound == [("run-1", result.task_id)]
