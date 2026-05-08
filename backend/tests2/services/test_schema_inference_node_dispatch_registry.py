"""Contract tests for node.type dispatch registry in schema inference."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.schema_inference import ColumnSchema, SchemaInferenceService


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_SCHEMA_INFER_PY = _BACKEND_ROOT / "app" / "core" / "projects" / "schema_inference.py"
_NODE_REGISTRY_BLOCK_RE = re.compile(
    r"#\s*NODE-DISPATCH-REGISTRY:start\s*\n(.*?)#\s*NODE-DISPATCH-REGISTRY:end",
    re.DOTALL,
)
_NODE_REGISTRY_KEY_RE = re.compile(r'^\s*"([a-zA-Z0-9_]+)"\s*:', re.MULTILINE)


def _node(node_id: str, node_type: str, **overrides):
    data = {
        "id": node_id,
        "type": node_type,
        "columns": None,
        "calculated_columns": None,
        "data": None,
        "config": None,
        "transform_op": None,
        "source_project_id": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _col(name: str, ctype: str = "str", **overrides):
    data = {
        "name": name,
        "alias": None,
        "type": ctype,
        "visible": True,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _parse_node_dispatch_registry() -> set[str]:
    src = _SCHEMA_INFER_PY.read_text(encoding="utf-8")
    match = _NODE_REGISTRY_BLOCK_RE.search(src)
    assert match is not None, "NODE-DISPATCH-REGISTRY sentinel missing"
    return set(_NODE_REGISTRY_KEY_RE.findall(match.group(1)))


def _pairs(schema: list[ColumnSchema]) -> list[tuple[str, str, bool]]:
    return [(col.name, col.type, col.is_calculated) for col in schema]


def test_node_dispatch_registry_contains_incrementally_migrated_types():
    assert _parse_node_dispatch_registry() == {"source", "file_source", "result"}


def test_source_node_registry_handler_matches_direct_source_schema():
    service = SchemaInferenceService(project_service=None)
    node = _node(
        "src1",
        "source",
        columns=[
            _col("id", "int"),
            _col("hidden", "str", visible=False),
            _col("raw_name", "str", alias="display_name"),
        ],
        calculated_columns=[
            {"name": "score", "output_type": "real"},
            "invalid",
        ],
    )

    via_registry = service._infer_node_schema(
        node=node,
        nodes_by_id={node.id: node},
        edges_by_target={},
        project_id="project-1",
    )
    direct = service._infer_source_schema(node)

    assert _pairs(via_registry) == _pairs(direct)
    assert [col.source_node for col in via_registry] == [node.id, node.id, node.id]


def test_file_source_node_registry_handler_matches_direct_file_source_schema():
    service = SchemaInferenceService(project_service=None)
    node = _node(
        "file1",
        "file_source",
        data={
            "columns": [
                {"name": "a", "type": "int"},
                {"name": "skip", "type": "str", "visible": False},
                {"name": "geom", "type": "geometry:point:4326"},
            ],
        },
    )

    via_registry = service._infer_node_schema(
        node=node,
        nodes_by_id={node.id: node},
        edges_by_target={},
        project_id="project-1",
    )
    direct = service._infer_file_source_schema(node)

    assert _pairs(via_registry) == _pairs(direct)
    assert [col.source_node for col in via_registry] == [node.id, node.id]


def test_result_node_registry_handler_preserves_passthrough_behavior():
    service = SchemaInferenceService(project_service=None)
    source = _node("src1", "source", columns=[_col("a", "int"), _col("b", "str")])
    result = _node("res1", "result")
    edge = SimpleNamespace(source="src1", target="res1")

    via_registry = service._infer_node_schema(
        node=result,
        nodes_by_id={source.id: source, result.id: result},
        edges_by_target={"res1": [edge]},
        project_id="project-1",
    )
    input_schema = service._infer_source_schema(source)

    assert _pairs(via_registry) == _pairs(input_schema)
    assert [col.source_node for col in via_registry] == [source.id, source.id]
