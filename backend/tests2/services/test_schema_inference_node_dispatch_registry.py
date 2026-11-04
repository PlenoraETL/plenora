"""Contract tests for node.type dispatch registry in schema inference."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.projects.schema_inference import ColumnSchema, SchemaInferenceService


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_SCHEMA_INFER_PY = _BACKEND_ROOT / "app" / "core" / "projects" / "schema_inference.py"
_SCHEMA_TRANSFORM_HANDLERS_PY = (
    _BACKEND_ROOT / "app" / "core" / "projects" / "schema_transform_handlers.py"
)
_SCHEMA_GEO_HANDLERS_PY = (
    _BACKEND_ROOT / "app" / "core" / "projects" / "schema_geo_handlers.py"
)
_PROJECT_CONTEXT_LOADER_PY = (
    _BACKEND_ROOT / "app" / "core" / "projects" / "project_context_loader.py"
)
_OLD_PROJECT_CONTEXT_PY = (
    _BACKEND_ROOT / "app" / "core" / "projects" / "schema_inference_project_context.py"
)
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


def _infer_node_schema_function() -> ast.FunctionDef:
    tree = ast.parse(_SCHEMA_INFER_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_infer_node_schema":
            return node
    raise AssertionError("_infer_node_schema function missing")


def _function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _is_node_type_attribute(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "type"
        and isinstance(node.value, ast.Name)
        and node.value.id == "node"
    )


def _pairs(schema: list[ColumnSchema]) -> list[tuple[str, str, bool]]:
    return [(col.name, col.type, col.is_calculated) for col in schema]


def test_node_dispatch_registry_contains_incrementally_migrated_types():
    assert _parse_node_dispatch_registry() == {
        "comment", "carica", "carica_arcgis",
        "source",
        "file_source",
        "datasource_node",
        "staticValues",
        "multi_join",
        "feature_source",
        "project_source",
        "project_input",
        "transform",
        "api_enrich",
        "result",
    }


def test_infer_node_schema_has_no_central_node_type_branches():
    """New node.type inference must be registered, not added centrally."""
    function = _infer_node_schema_function()
    violations: list[int] = []

    for node in ast.walk(function):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        if any(_is_node_type_attribute(operand) for operand in operands):
            violations.append(node.lineno)

    assert violations == [], (
        "Add node.type schema handlers to _build_node_type_handlers instead "
        f"of branching centrally in _infer_node_schema. Lines: {violations}"
    )


def test_schema_inference_delegates_transform_schema_handlers_to_module():
    """Transform-specific `_schema_*` methods live in the handler module."""
    service_functions = _function_names(_SCHEMA_INFER_PY)
    handler_functions = _function_names(_SCHEMA_TRANSFORM_HANDLERS_PY)

    assert not {
        name for name in service_functions if name.startswith("_schema_")
    }, "schema_inference.py must not own transform `_schema_*` handlers"
    assert "_schema_join" in handler_functions
    assert "_schema_passthrough" in handler_functions


def test_geo_transform_schema_handlers_live_in_geo_module():
    """Geo/spatial schema handlers stay out of the root transform module."""
    root_functions = _function_names(_SCHEMA_TRANSFORM_HANDLERS_PY)
    geo_functions = _function_names(_SCHEMA_GEO_HANDLERS_PY)

    expected_geo_handlers = {
        "_schema_sjoin",
        "_schema_geo_area",
        "_schema_geo_boundary",
        "_schema_geo_bounds_extractor",
        "_schema_geo_buffer",
        "_schema_geo_builder",
        "_schema_geo_centroid",
        "_schema_geo_clip",
        "_schema_geo_convex_hull",
        "_schema_geo_count_points_in_polygons",
        "_schema_geo_dissolve",
        "_schema_geo_distance",
        "_schema_geo_from_coords",
        "_schema_geo_length",
        "_schema_geo_line_builder",
        "_schema_geo_nearest",
        "_schema_geo_overlay",
        "_schema_geo_perimeter",
        "_schema_geo_point_on_surface",
        "_schema_geo_polygon_builder",
        "_schema_geo_to_wkt",
        "_schema_geo_vertex_count",
        "_schema_geo_voronoi",
        "_schema_geo_within",
    }

    assert not {
        name
        for name in root_functions
        if name.startswith("_schema_geo_") or name == "_schema_sjoin"
    }
    assert expected_geo_handlers <= geo_functions


def test_schema_inference_does_not_import_projects_db_directly():
    tree = ast.parse(_SCHEMA_INFER_PY.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "app.core.projects.db":
                violations.append("from app.core.projects.db import ...")
            if node.module and re.fullmatch(r"app\.core\.[^.]+\.db", node.module):
                violations.append(f"from {node.module} import ...")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app.core.projects.db":
                    violations.append("import app.core.projects.db")
                if re.fullmatch(r"app\.core\.[^.]+\.db", alias.name):
                    violations.append(f"import {alias.name}")

    assert violations == [], (
        "schema_inference must use project_context_loader as its explicit "
        f"persistence boundary instead of importing db modules: {violations}"
    )


def test_project_context_loader_is_the_only_schema_inference_db_boundary():
    assert _PROJECT_CONTEXT_LOADER_PY.exists()
    assert not _OLD_PROJECT_CONTEXT_PY.exists()

    infer_tree = ast.parse(_SCHEMA_INFER_PY.read_text(encoding="utf-8"))
    loader_tree = ast.parse(_PROJECT_CONTEXT_LOADER_PY.read_text(encoding="utf-8"))

    infer_imports = [
        node.module
        for node in ast.walk(infer_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    ]
    assert "app.core.projects.project_context_loader" in infer_imports
    assert "app.core.projects.schema_inference_project_context" not in infer_imports

    db_imports: list[str] = []
    for node in ast.walk(loader_tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if re.fullmatch(r"app\.core\.[^.]+\.db", node.module):
                db_imports.append(node.module)
        if isinstance(node, ast.Import):
            db_imports.extend(
                alias.name
                for alias in node.names
                if re.fullmatch(r"app\.core\.[^.]+\.db", alias.name)
            )

    assert db_imports == [], (
        "project_context_loader may depend on the project-owned db package, "
        f"but not direct cross-domain db modules: {db_imports}"
    )


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


def test_feature_source_node_registry_handler_infers_declared_columns():
    # feature_source (ArcGIS feature layer) porta colonne dichiarate sul nodo
    # nella stessa forma di file_source; la registry deve inferirle (non saltarle).
    service = SchemaInferenceService(project_service=None)
    node = _node(
        "feat1",
        "feature_source",
        data={
            "columns": [
                {"name": "objectid", "type": "int"},
                {"name": "skip", "type": "str", "visible": False},
                {"name": "geom", "type": "geometry:polygon:4326"},
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
    assert _pairs(via_registry) == [("objectid", "int", False), ("geom", "geometry:polygon:4326", False)]
    assert [col.source_node for col in via_registry] == [node.id, node.id]


def test_project_source_node_registry_handler_uses_project_context_boundary(
    monkeypatch,
):
    service = SchemaInferenceService(project_service=None)
    node = _node(
        "project_src1",
        "project_source",
        source_project_id="upstream-1",
        columns=[
            {"name": "id", "visible": True},
            {"name": "hidden", "visible": False},
        ],
    )
    calls: list[str] = []

    def fake_load_project_source_schema_context(project_id: str):
        calls.append(project_id)
        return SimpleNamespace(
            project_id=project_id,
            output_contract={
                "columns": [
                    {
                        "id": "col-id",
                        "source_name": "id",
                        "physical_name": "id",
                        "display_name": "id",
                        "type": {"logical": "int"},
                        "ordinal": 0,
                        "status": "active",
                    },
                    {
                        "id": "col-hidden",
                        "source_name": "hidden",
                        "physical_name": "hidden",
                        "display_name": "hidden",
                        "type": {"logical": "str"},
                        "ordinal": 1,
                        "status": "active",
                    },
                ],
            },
        )

    monkeypatch.setattr(
        "app.core.projects.schema_inference.load_project_source_schema_context",
        fake_load_project_source_schema_context,
    )

    via_registry = service._infer_node_schema(
        node=node,
        nodes_by_id={node.id: node},
        edges_by_target={},
        project_id="project-1",
    )

    assert calls == ["upstream-1"]
    assert _pairs(via_registry) == [("id", "int", False)]
    assert [col.source_node for col in via_registry] == [node.id]


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


def test_transform_node_registry_handler_preserves_transform_dispatch():
    service = SchemaInferenceService(project_service=None)
    source = _node("src1", "source", columns=[_col("a", "int"), _col("b", "str")])
    transform = _node(
        "tr1",
        "transform",
        transform_op="rename",
        config={"columns": {"a": "renamed_a"}},
    )
    edge = SimpleNamespace(source="src1", target="tr1")

    via_registry = service._infer_node_schema(
        node=transform,
        nodes_by_id={source.id: source, transform.id: transform},
        edges_by_target={"tr1": [edge]},
        project_id="project-1",
    )
    direct = service._infer_transform_schema(
        transform,
        [service._infer_source_schema(source)],
        {"tr1": [edge]},
    )

    assert _pairs(via_registry) == _pairs(direct)
    assert [col.source_node for col in via_registry] == [transform.id, transform.id]


def test_api_enrich_node_registry_handler_preserves_api_schema():
    service = SchemaInferenceService(project_service=None)
    source = _node("src1", "source", columns=[_col("id", "int")])
    api_node = _node(
        "api1",
        "api_enrich",
        data={
            "output_mapping": [
                {"column": "score", "type": "real"},
                {"target_column": "status"},
            ],
        },
    )
    edge = SimpleNamespace(source="src1", target="api1")

    via_registry = service._infer_node_schema(
        node=api_node,
        nodes_by_id={source.id: source, api_node.id: api_node},
        edges_by_target={"api1": [edge]},
        project_id="project-1",
    )
    direct = service._infer_api_enrich_schema(
        api_node,
        [service._infer_source_schema(source)],
    )

    assert _pairs(via_registry) == _pairs(direct)
    assert [col.source_node for col in via_registry] == [
        api_node.id,
        api_node.id,
    ]
