"""Architecture guard for schema-inference handler composition.

The schema-inference dispatcher must stay Go-ready: the coordinator can
delegate to small handler objects, but it must not aggregate behavior via
multiple inheritance or schema-handler mixin bases.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.shared]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_PROJECTS_ROOT = _BACKEND_ROOT / "app" / "core" / "projects"
_TRANSFORM_HANDLERS = _PROJECTS_ROOT / "schema_transform_handlers.py"
_SCHEMA_INFERENCE = _PROJECTS_ROOT / "schema_inference.py"

_SCHEMA_HANDLER_BASE_NAMES = frozenset(
    {
        "JoinSchemaHandlers",
        "GeoSchemaHandlers",
        "ReshapeSchemaHandlers",
        "FormulaSchemaHandlers",
        "TableSchemaHandlers",
    }
)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8-sig"))


def _class_def(tree: ast.AST, name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"Class {name!r} not found")


def _base_name(base: ast.expr) -> str:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    if isinstance(base, ast.Subscript):
        return _base_name(base.value)
    if isinstance(base, ast.Call):
        return _base_name(base.func)
    return ast.unparse(base)


def _self_assignments(class_def: ast.ClassDef) -> set[str]:
    assignments: set[str] = set()
    for node in class_def.body:
        if not isinstance(node, ast.FunctionDef) or node.name != "__init__":
            continue
        for child in ast.walk(node):
            targets: list[ast.expr] = []
            if isinstance(child, ast.Assign):
                targets.extend(child.targets)
            elif isinstance(child, ast.AnnAssign):
                targets.append(child.target)
            elif isinstance(child, ast.AugAssign):
                targets.append(child.target)
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    assignments.add(target.attr)
    return assignments


def test_transform_schema_handlers_has_no_schema_handler_bases():
    """TransformSchemaHandlers is a coordinator, not a mixin pile.

    It may expose the public transform dispatch boundary, but concrete
    join/geo/reshape/formula/table behavior must be reached through
    explicit collaborators instead of Python MRO.
    """
    class_def = _class_def(_parse(_TRANSFORM_HANDLERS), "TransformSchemaHandlers")
    bases = {_base_name(base) for base in class_def.bases}
    schema_bases = bases & _SCHEMA_HANDLER_BASE_NAMES

    assert not schema_bases, (
        "TransformSchemaHandlers must not inherit schema handler bases. "
        "Use composition fields such as join/geo/reshape/formula/table "
        f"collaborators instead. Current schema bases: {sorted(schema_bases)}"
    )
    assert len(class_def.bases) <= 1, (
        "TransformSchemaHandlers must not reintroduce multiple inheritance "
        f"or mixin aggregation. Current bases: {sorted(bases)}"
    )


def test_transform_schema_handlers_exposes_explicit_collaborators():
    """Positive lock: the coordinator owns collaborators, not inherited methods."""
    class_def = _class_def(_parse(_TRANSFORM_HANDLERS), "TransformSchemaHandlers")
    assignments = _self_assignments(class_def)
    expected_collaborators = {
        "_join_handlers",
        "_geo_handlers",
        "_reshape_handlers",
        "_formula_handlers",
        "_table_handlers",
    }

    assert expected_collaborators <= assignments, (
        "TransformSchemaHandlers must wire explicit schema handler "
        "collaborators in __init__ so dispatch does not depend on MRO. "
        f"Missing: {sorted(expected_collaborators - assignments)}"
    )


def test_schema_inference_coordinators_do_not_use_multiple_handler_inheritance():
    """Coordinator classes must stay composition-only.

    This catches future attempts to move the same mixin aggregation from
    TransformSchemaHandlers into SchemaInferenceService or another local
    coordinator.
    """
    checked_files = (_TRANSFORM_HANDLERS, _SCHEMA_INFERENCE)
    violations: list[str] = []

    for path in checked_files:
        tree = _parse(path)
        rel = path.relative_to(_BACKEND_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {_base_name(base) for base in node.bases}
            schema_bases = bases & _SCHEMA_HANDLER_BASE_NAMES
            if len(node.bases) > 1 or schema_bases:
                violations.append(
                    f"{rel}:{node.lineno}:{node.name} "
                    f"bases={sorted(bases)} schema_bases={sorted(schema_bases)}"
                )

    assert not violations, (
        "Schema inference coordinators must not aggregate handler behavior "
        "through inheritance. Use explicit collaborator objects instead.\n"
        f"Violations: {violations}"
    )
