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


# Test 4 — project_source preferisce result_node.columns quando presente


def test_infer_project_source_schema_prefers_result_node_columns_over_metadata(
    monkeypatch,
):
    """Quando il canvas del progetto sorgente ha
    `nodes[result].columns` popolato (post-C9), questo prevale su
    `canvas.columns_metadata` (anche se lo metadata e' stale)."""
    svc = _svc()

    # Stub the current project metadata DB boundary: il source progetto ha result.columns
    # corrette (4 col) e columns_metadata stale (2 col legacy).
    fake_canvas = {
        "nodes": [
            {
                "id": "src1",
                "type": "file_source",
                "columns": [{"name": "id", "type": "int", "visible": True}],
            },
            {
                "id": "res1",
                "type": "result",
                "columns": [
                    {"name": "id", "type": "int", "visible": True},
                    {"name": "lat", "type": "float", "visible": True},
                    {"name": "risk", "type": "str", "visible": True},
                    {"name": "score", "type": "float", "visible": True},
                ],
            },
        ],
        "columns_metadata": [
            # STALE — solo 2 col, perse risk/score
            {"name": "id", "type": "int"},
            {"name": "lat", "type": "float"},
        ],
    }

    from app.core.projects.project_context_loader import (
        ProjectSourceSchemaContext,
    )
    monkeypatch.setattr(
        "app.core.projects.schema_inference.load_project_source_schema_context",
        lambda pid: ProjectSourceSchemaContext(project_id=pid, canvas=fake_canvas),
    )
    ps_node = _node("ps1", "project_source", source_project_id="src-proj-1")
    schema = svc._infer_project_source_schema(ps_node)

    names = [c.name for c in schema]
    # Deve restituire le 4 col del result_node, non le 2 stale di metadata
    assert names == ["id", "lat", "risk", "score"], names


# Test 5 — project_source fallback su columns_metadata se result.columns vuoto


def test_infer_project_source_schema_fallback_to_columns_metadata_when_result_empty(
    monkeypatch,
):
    """Compat legacy: progetti pre-C9 / canvas mai salvati hanno
    `result_node.columns = []` ma `columns_metadata` popolato. Il
    reader cade su metadata."""
    svc = _svc()

    fake_canvas = {
        "nodes": [
            {
                "id": "res1",
                "type": "result",
                "columns": [],  # NOTE: legacy / mai computed
            }
        ],
        "columns_metadata": [
            {"name": "id", "type": "int"},
            {"name": "value", "type": "str"},
        ],
    }

    from app.core.projects.project_context_loader import (
        ProjectSourceSchemaContext,
    )
    monkeypatch.setattr(
        "app.core.projects.schema_inference.load_project_source_schema_context",
        lambda pid: ProjectSourceSchemaContext(project_id=pid, canvas=fake_canvas),
    )
    ps_node = _node("ps1", "project_source", source_project_id="src-proj-1")
    schema = svc._infer_project_source_schema(ps_node)

    names = [c.name for c in schema]
    assert names == ["id", "value"], names


# Test 6 — rollback semantica fuori scope: alias non usato come nome
# Pre-fix `columns_metadata` non aveva campo `alias`, quindi il consumer
# riceveva sempre `name`. Il primo tentativo di Fix 2 introduceva
# `col_name = alias or name`: scope-creep che avrebbe rotto i consumer
# che matchano via `visible_columns` (set di `name`). Rollback in commit
# 2c0a071 ripristina la semantica 1:1 con pre-fix.


def test_infer_project_source_schema_does_not_rename_columns_via_alias(
    monkeypatch,
):
    """Anche se result_node.columns ha `alias`, il consumer riceve
    `name` (compat 1:1 con pre-fix che leggeva da columns_metadata)."""
    svc = _svc()

    fake_canvas = {
        "nodes": [
            {
                "id": "res1",
                "type": "result",
                "columns": [
                    # User ha rinominato "x" → "X_renamed" via alias
                    {"name": "x", "alias": "X_renamed", "type": "int", "visible": True},
                    {"name": "y", "type": "str", "visible": True},
                ],
            }
        ],
        "columns_metadata": [],
    }

    from app.core.projects.project_context_loader import (
        ProjectSourceSchemaContext,
    )
    monkeypatch.setattr(
        "app.core.projects.schema_inference.load_project_source_schema_context",
        lambda pid: ProjectSourceSchemaContext(project_id=pid, canvas=fake_canvas),
    )
    ps_node = _node("ps1", "project_source", source_project_id="src-proj-1")
    schema = svc._infer_project_source_schema(ps_node)

    names = [c.name for c in schema]
    assert names == ["x", "y"], names
    assert "X_renamed" not in names


# Test 7 — rollback semantica fuori scope: niente filtro intrinseco visible=False
# Pre-fix `columns_metadata` non aveva campo `visible`, quindi tutte le
# colonne dei metadata venivano sempre emesse. Il primo tentativo di
# Fix 2 introduceva `if not col.get("visible", True): continue` che
# avrebbe nascosto colonne pre-fix sempre visibili. Rollback ripristina:
# il filtro visibility resta SOLO via `node_columns` (lato consumer
# project_source), non via campo intrinseco della colonna source.


def test_infer_project_source_schema_does_not_filter_by_intrinsic_visible_flag(
    monkeypatch,
):
    """Una colonna con `visible: False` in result_node.columns deve
    comunque essere emessa, perche' pre-fix (lettura da metadata senza
    campo visible) la includeva sempre. Il filtraggio visibility
    avviene solo via project_source node.columns (consumer-side)."""
    svc = _svc()

    fake_canvas = {
        "nodes": [
            {
                "id": "res1",
                "type": "result",
                "columns": [
                    {"name": "shown", "type": "int", "visible": True},
                    # visible: False — il consumer deve comunque vederla
                    {"name": "hidden_in_source", "type": "str", "visible": False},
                ],
            }
        ],
        "columns_metadata": [],
    }

    from app.core.projects.project_context_loader import (
        ProjectSourceSchemaContext,
    )
    monkeypatch.setattr(
        "app.core.projects.schema_inference.load_project_source_schema_context",
        lambda pid: ProjectSourceSchemaContext(project_id=pid, canvas=fake_canvas),
    )
    # Consumer node senza filtro proprio (node.columns vuoto): tutte
    # le colonne del source devono passare.
    ps_node = _node("ps1", "project_source", source_project_id="src-proj-1")
    schema = svc._infer_project_source_schema(ps_node)

    names = [c.name for c in schema]
    assert names == ["shown", "hidden_in_source"], names
