from __future__ import annotations

import pytest

from app.core.eseguibili import db as eseguibili_db
from app.core.eseguibili import runtime as executable_runtime
from app.core.eseguibili import service as executable_service
from app.core.eseguibili.manifest import compile_manifest


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
