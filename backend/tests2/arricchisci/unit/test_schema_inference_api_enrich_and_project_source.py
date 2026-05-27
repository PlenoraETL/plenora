"""
Contract tests — `SchemaInferenceService` per api_enrich + project_source.

Bug 2026-04-30 (cross-modulo result columns):
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Helpers


def _svc():
    from app.core.projects.schema_inference import SchemaInferenceService
    return SchemaInferenceService(project_service=MagicMock())


def _node(node_id: str, ntype: str, **data):
    """Mock Node con .id/.type/.data + attributi top-level usati dal
    dispatch (per evitare di costruire il Pydantic Node per ogni test —
    il service usa attributi tramite getattr sull'oggetto)."""
    n = MagicMock()
    n.id = node_id
    n.type = ntype
    n.data = data.get("data", {}) or {}
    n.columns = data.get("columns", []) or []
    n.calculated_columns = data.get("calculated_columns", []) or []
    n.source_project_id = data.get("source_project_id")
    n.file_source_id = data.get("file_source_id")
    n.transform_op = data.get("transform_op")
    n.config = data.get("config")
    return n


# Test 1 — api_enrich con output_mapping standard (chiave `column`)


def test_infer_api_enrich_schema_reads_output_mapping_canonical_column_key():
    from app.core.projects.schema_inference import ColumnSchema

    svc = _svc()
    base_input = [
        ColumnSchema(name="id", type="int", source_node="src"),
        ColumnSchema(name="lat", type="float", source_node="src"),
    ]
    api_node = _node(
        "api1",
        "api_enrich",
        data={
            "output_mapping": [
                {"column": "risk_level", "type": "str", "path": "$.risk"},
                {"column": "score", "type": "float", "path": "$.score"},
            ]
        },
    )

    schema = svc._infer_api_enrich_schema(api_node, [base_input])

    names = [c.name for c in schema]
    assert names == ["id", "lat", "risk_level", "score"]
    by_name = {c.name: c for c in schema}
    assert by_name["risk_level"].type == "str"
    assert by_name["risk_level"].is_calculated is True
    assert by_name["score"].type == "float"
    # Input columns preservano is_calculated originale
    assert by_name["id"].is_calculated is False


# Test 2 — api_enrich shape variante: target_column / name / type assente


def test_infer_api_enrich_schema_tolerates_target_column_and_name_fallbacks():
    from app.core.projects.schema_inference import ColumnSchema

    svc = _svc()
    base_input = [ColumnSchema(name="id", type="int", source_node="src")]
    api_node = _node(
        "api1",
        "api_enrich",
        data={
            "output_mapping": [
                # Variante FE storica: target_column al posto di column
                {"target_column": "via_target", "path": "$.a"},
                # Variante: solo name
                {"name": "via_name", "path": "$.b"},
                # Mapping malformato (senza chiavi nome) → skip
                {"path": "$.c"},
                # Mapping non-dict → skip
                "not_a_dict",
                # Duplicato di colonna input → skip (no override)
                {"column": "id"},
                # type assente → default 'str'
                {"column": "no_type", "path": "$.d"},
            ]
        },
    )

    schema = svc._infer_api_enrich_schema(api_node, [base_input])

    names = [c.name for c in schema]
    assert names == ["id", "via_target", "via_name", "no_type"]
    by_name = {c.name: c for c in schema}
    assert by_name["no_type"].type == "str"
    # Input column "id" non e' stata sovrascritta
    assert by_name["id"].is_calculated is False


# Test 3 — compute_result_columns end-to-end: project_source → api_enrich → result


def test_compute_result_columns_includes_api_enrich_added_columns():
    """Regression del bug originale (C9 fix + Fix 1 combinati): il
    canvas con un api_enrich a monte del result deve produrre risultato
    `input_cols + output_mapping_cols` quando passato a
    `compute_result_columns`."""
    from app.shared.execution.canvas_columns import compute_result_columns

    # Mock project_service: project_source upstream non risolve niente
    # (lo testiamo separatamente in Test 4/5). Qui usiamo file_source
    # come sorgente per evitare DB roundtrip.
    project_service = MagicMock()

    file_node = _node(
        "f1",
        "file_source",
        data={
            "columns": [
                {"name": "id", "type": "int", "visible": True},
                {"name": "lat", "type": "float", "visible": True},
            ]
        },
    )
    api_node = _node(
        "a1",
        "api_enrich",
        data={
            "output_mapping": [
                {"column": "risk", "type": "str"},
                {"column": "score", "type": "float"},
            ]
        },
    )
    result_node = _node("r1", "result", data={})

    canvas = MagicMock()
    canvas.nodes = [file_node, api_node, result_node]

    e1 = MagicMock(); e1.source = "f1"; e1.target = "a1"
    e2 = MagicMock(); e2.source = "a1"; e2.target = "r1"
    canvas.edges = [e1, e2]

    out = compute_result_columns(
        canvas=canvas,
        result_id="r1",
        project_id="proj-test",
        project_service=project_service,
        schema_inference_service_factory=(
            lambda service: __import__(
                "app.core.projects.schema_inference",
                fromlist=["SchemaInferenceService"],
            ).SchemaInferenceService(service)
        ),
    )

    names = [c["name"] for c in out]
    assert names == ["id", "lat", "risk", "score"], names
    by_name = {c["name"]: c for c in out}
    assert by_name["risk"]["type"] == "str"
    assert by_name["score"]["type"] == "float"
    assert by_name["lat"]["type"] == "float"


# Test 4 — project_source legge solo l'output_contract pubblico


def test_infer_project_source_schema_reads_output_contract(
    monkeypatch,
):
    svc = _svc()

    output_contract = {
        "schema_version": 1,
        "producer_project_id": "src-proj-1",
        "revision": 3,
        "columns": [
            {
                "id": "col-id",
                "source_name": "id",
                "physical_name": "myid",
                "display_name": "myid",
                "type": {"logical": "integer"},
                "ordinal": 0,
                "status": "active",
            },
            {
                "id": "col-score",
                "source_name": "score",
                "physical_name": "score",
                "display_name": "score",
                "type": {"logical": "number"},
                "ordinal": 1,
                "status": "active",
            },
        ],
    }

    from app.core.projects.project_context_loader import (
        ProjectSourceSchemaContext,
    )
    monkeypatch.setattr(
        "app.core.projects.schema_inference.load_project_source_schema_context",
        lambda pid: ProjectSourceSchemaContext(
            project_id=pid,
            output_contract=output_contract,
        ),
    )
    ps_node = _node("ps1", "project_source", source_project_id="src-proj-1")
    schema = svc._infer_project_source_schema(ps_node)

    names = [c.name for c in schema]
    assert names == ["myid", "score"], names
    assert [c.type for c in schema] == ["integer", "number"]


# Test 5 — project_source senza output_contract resta graceful


def test_infer_project_source_schema_without_output_contract_returns_empty_schema(
    monkeypatch,
):
    svc = _svc()

    from app.core.projects.project_context_loader import (
        ProjectSourceSchemaContext,
    )
    monkeypatch.setattr(
        "app.core.projects.schema_inference.load_project_source_schema_context",
        lambda pid: ProjectSourceSchemaContext(
            project_id=pid,
            output_contract=None,
        ),
    )
    ps_node = _node("ps1", "project_source", source_project_id="src-proj-1")
    assert svc._infer_project_source_schema(ps_node) == []
