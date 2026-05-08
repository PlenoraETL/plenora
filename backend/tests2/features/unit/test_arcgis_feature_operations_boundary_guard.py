"""Architecture guards for the ArcGIS feature operations provider boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.features, pytest.mark.contract]

_BACKEND_DIR = Path(__file__).parents[3]
_OPERATIONS_PATH = (
    _BACKEND_DIR / "app" / "core" / "features" / "arcgis_feature_operations.py"
)


def _parse_operations() -> ast.Module:
    return ast.parse(_OPERATIONS_PATH.read_text(encoding="utf-8"))


def test_arcgis_feature_operations_does_not_import_concrete_client() -> None:
    """Operations must depend on shared provider protocols, not ArcGISClient."""
    tree = _parse_operations()
    violations: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if node.module == "app.core.features.arcgis_client" or alias.name == "ArcGISClient":
                    violations.append((node.lineno, f"{node.module}.{alias.name}"))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app.core.features.arcgis_client":
                    violations.append((node.lineno, alias.name))

    assert not violations, (
        "arcgis_feature_operations.py must not import ArcGISClient; inject "
        f"app.shared.connections.arcgis_provider protocols instead. Violations: {violations}"
    )


def test_arcgis_feature_operations_does_not_call_private_provider_methods() -> None:
    """Operations may call only explicit provider protocol methods."""
    tree = _parse_operations()
    violations: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        receiver = node.func.value
        if (
            isinstance(receiver, ast.Attribute)
            and isinstance(receiver.value, ast.Name)
            and receiver.value.id == "self"
            and receiver.attr == "_client"
            and node.func.attr.startswith("_")
        ):
            violations.append((node.lineno, node.func.attr))

    assert not violations, (
        "arcgis_feature_operations.py must call explicit provider protocol "
        f"methods only, not private provider methods. Violations: {violations}"
    )
