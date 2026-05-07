"""
Structural boundary guard for CanvasNode and CanvasGraph.

BE-GO-READY-ARCH-060-W1-CANVASGRAPH-STRUCTURAL-GUARD.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.shared]

_EXECUTION_ROOT = Path(__file__).parents[2] / "app" / "shared" / "execution"

_STRUCTURAL_MODULES = (
    "canvas_node.py",
    "canvas_graph.py",
)

# Forbidden as substrings of any function/class/parameter name.
# Short tokens (join, crs) are intentional: they guard against method names like
# apply_join(), get_crs_column(), etc. while not touching Python builtins since
# those appear only as ast.Attribute (call-site), not at definition sites.
_FORBIDDEN_TOKENS = (
    "file_source_id",
    "api_id",
    "arcgis",
    "join",
    "crs",
    "transform",
    "load_",
    "validate_publish",
)


def _definition_site_names(path: Path) -> list[str]:
    """Collect identifiers from definition sites: functions, classes, parameters.

    Excludes attribute accesses (.attr) and local variable names (Name.id) to
    avoid false positives from legitimate Python built-ins and generic
    assignments.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
        elif isinstance(node, ast.ClassDef):
            names.append(node.name)
        elif isinstance(node, ast.arg):
            names.append(node.arg)
    return names


@pytest.mark.parametrize("module_name", _STRUCTURAL_MODULES, ids=lambda m: m)
def test_canvas_structural_module_has_no_business_identifiers(module_name: str) -> None:
    """canvas_node.py and canvas_graph.py must define only structural identifiers.

    Adding a function/method/parameter whose name contains a forbidden token
    means business-specific behaviour is leaking into a shared structural module.
    Move that logic to the domain module that owns the node type.
    """
    path = _EXECUTION_ROOT / module_name
    assert path.exists(), f"Module {module_name} not found at {path}"

    names = _definition_site_names(path)
    violations: list[tuple[str, str]] = []
    for name in names:
        lower = name.lower()
        for token in _FORBIDDEN_TOKENS:
            if token in lower:
                violations.append((name, token))
                break  # one token per name is enough to report

    assert not violations, (
        f"app/shared/execution/{module_name} defines business-specific identifiers:\n"
        + "\n".join(
            f"  identifier={name!r}  token={token!r}"
            for name, token in violations
        )
        + "\n\nAllowed structural vocabulary: id, type, data, get_field, raw, edges, "
        "result, from_nodes_edges, raw_map.\n"
        "Forbidden business concepts belong in the domain module that owns the node "
        "type. See BE-GO-READY-ARCH-060-W1 and BE_RULES.md."
    )


# Sanity: the modules themselves must actually exist and be parseable


@pytest.mark.parametrize("module_name", _STRUCTURAL_MODULES, ids=lambda m: m)
def test_canvas_structural_module_is_parseable(module_name: str) -> None:
    path = _EXECUTION_ROOT / module_name
    assert path.exists(), f"{module_name} missing"
    try:
        ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as e:
        pytest.fail(f"{module_name} has a syntax error: {e}")


# Allowed vocabulary: structural names expected to always be present


def test_canvas_node_exposes_structural_interface() -> None:
    """CanvasNode must expose id, type, data, get_field."""
    path = _EXECUTION_ROOT / "canvas_node.py"
    names = set(_definition_site_names(path))
    for expected in ("id", "type", "data", "get_field"):
        assert expected in names, (
            f"CanvasNode is missing expected structural name {expected!r}. "
            "Do not remove structural interface without updating this guard."
        )


def test_canvas_graph_exposes_structural_interface() -> None:
    """CanvasGraph must expose from_canvas, raw, get, result_node, edges."""
    path = _EXECUTION_ROOT / "canvas_graph.py"
    names = set(_definition_site_names(path))
    for expected in ("from_canvas", "from_nodes_edges", "raw", "get", "result_node"):
        assert expected in names, (
            f"CanvasGraph is missing expected structural name {expected!r}. "
            "Do not remove structural interface without updating this guard."
        )
