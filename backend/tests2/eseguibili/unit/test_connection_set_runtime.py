from __future__ import annotations

import pytest

from app.core.connections.models import ConnectionSetOption
from app.core.eseguibili import runtime
from app.core.eseguibili.manifest import compile_manifest
from app.core.eseguibili.service import (
    hydrate_manifest_connection_options,
    resolve_runtime_connection_id,
)
from app.core.orchestrator.execution_contract import PipelineExecutionContract
from app.core.orchestrator.runtime_inputs import apply_project_runtime_inputs


def _canvas() -> dict:
    return {
        "nodes": [
            {
                "id": "input_cliente",
                "type": "project_input",
                "position": {"x": 0, "y": 0},
                "input_key": "cliente",
                "label": "Cliente",
                "input_kind": "connection",
                "connection_set_id": "cset_clienti",
                "required": True,
                "bindings": [],
            },
            {
                "id": "input_stato",
                "type": "project_input",
                "position": {"x": 0, "y": 100},
                "input_key": "stato",
                "label": "Stato",
                "input_kind": "text",
                "required": True,
                "bindings": [
                    {
                        "target_node_id": "orders",
                        "target_field": "filters.0.value",
                    }
                ],
            },
            {
                "id": "orders",
                "type": "source",
                "position": {"x": 200, "y": 0},
                "table_name": "orders",
                "filters": [
                    {"column": "status", "operator": "=", "value": ""}
                ],
            },
            {
                "id": "result",
                "type": "result",
                "position": {"x": 400, "y": 0},
            },
        ],
        "edges": [],
    }


def test_connection_selector_and_filter_binding_compile_and_resolve(monkeypatch):
    manifest = compile_manifest(
        source_project_id="project",
        project_type="estrai",
        canvas=_canvas(),
        default_connection_id="reference_connection",
    )

    connection_input = next(
        item for item in manifest.inputs if item.kind == "connection"
    )
    assert connection_input.key == "cliente"
    assert connection_input.connection_set_id == "cset_clienti"
    assert all(binding.input_key != "cliente" for binding in manifest.bindings)

    resolved, cleanup_ids, summary = runtime.resolve_runtime_inputs(
        eseguibile_id="exec",
        canvas=_canvas(),
        manifest=manifest,
        values={"cliente": "cliente_b", "stato": "OPEN"},
        group_id="group_a",
    )
    assert cleanup_ids == []
    orders = next(node for node in resolved["nodes"] if node["id"] == "orders")
    assert orders["filters"][0]["value"] == "OPEN"
    assert {node["id"] for node in resolved["nodes"]}.isdisjoint(
        {"input_cliente", "input_stato"}
    )
    assert summary["cliente"]["provided"] is True

    monkeypatch.setattr(
        "app.core.eseguibili.service.connection_sets.resolve_connection",
        lambda set_id, key, *, expected_group_id: (
            "connection_cliente_b"
            if (set_id, key, expected_group_id)
            == ("cset_clienti", "cliente_b", "group_a")
            else None
        ),
    )
    assert resolve_runtime_connection_id(
        manifest,
        raw_inputs={"cliente": "cliente_b", "stato": "OPEN"},
        group_id="group_a",
    ) == "connection_cliente_b"


def test_connection_selector_cannot_bind_to_a_node():
    canvas = _canvas()
    canvas["nodes"][0]["bindings"] = [
        {"target_node_id": "orders", "target_field": "filters.0.value"}
    ]
    with pytest.raises(ValueError, match="parametro di progetto"):
        compile_manifest(
            source_project_id="project",
            project_type="estrai",
            canvas=canvas,
            default_connection_id="reference_connection",
        )


def test_connection_options_are_hydrated_without_exposing_connection_ids(
    monkeypatch,
):
    manifest = compile_manifest(
        source_project_id="project",
        project_type="estrai",
        canvas=_canvas(),
        default_connection_id="reference_connection",
    )
    monkeypatch.setattr(
        "app.core.eseguibili.service.connection_sets.connection_set_options",
        lambda set_id, *, expected_group_id: [
            ConnectionSetOption(key="cliente_a", label="Cliente A"),
            ConnectionSetOption(key="cliente_b", label="Cliente B"),
        ],
    )
    hydrated = hydrate_manifest_connection_options(manifest, group_id="group_a")
    connection_input = next(
        item for item in hydrated.inputs if item.kind == "connection"
    )
    assert [item.model_dump() for item in connection_input.connection_options] == [
        {"key": "cliente_a", "label": "Cliente A"},
        {"key": "cliente_b", "label": "Cliente B"},
    ]
    assert "connection_id" not in connection_input.model_dump_json()


def test_project_execution_contract_freezes_resolved_connection(monkeypatch):
    contract = PipelineExecutionContract(
        project_id="project",
        project_type="estrai",
        group_id="group_a",
        canvas=_canvas(),
        artifact_refs={},
        runtime_context={"default_connection_id": "reference_connection"},
    )
    monkeypatch.setattr(
        "app.core.orchestrator.runtime_inputs.resolve_runtime_connection_id",
        lambda manifest, *, raw_inputs, group_id: "connection_cliente_b",
    )

    resolved = apply_project_runtime_inputs(
        contract,
        {"cliente": "cliente_b", "stato": "OPEN"},
    )

    assert resolved.runtime_context["default_connection_id"] == "connection_cliente_b"
    orders = next(node for node in resolved.canvas["nodes"] if node["id"] == "orders")
    assert orders["filters"][0]["value"] == "OPEN"
