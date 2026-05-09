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
_CLIENT_PATH = _BACKEND_DIR / "app" / "core" / "features" / "arcgis_client.py"
_WRITER_PATH = _BACKEND_DIR / "app" / "core" / "features" / "arcgis_feature_writer.py"
_WRITE_METHODS_PATH = (
    _BACKEND_DIR / "app" / "core" / "features" / "arcgis_feature_write_methods.py"
)


def _parse_operations() -> ast.Module:
    return ast.parse(_OPERATIONS_PATH.read_text(encoding="utf-8"))


def _parse_writer() -> ast.Module:
    return ast.parse(_WRITER_PATH.read_text(encoding="utf-8"))


def _parse_client() -> ast.Module:
    return ast.parse(_CLIENT_PATH.read_text(encoding="utf-8"))


def _parse_write_methods() -> ast.Module:
    return ast.parse(_WRITE_METHODS_PATH.read_text(encoding="utf-8"))


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


def test_arcgis_feature_operations_has_no_write_or_publish_methods() -> None:
    """Read/discovery operations must not grow back feature write orchestration."""
    tree = _parse_operations()
    forbidden_names = {
        "create_service_from_df",
        "delete_features",
        "resolve_objectids_for_update",
        "split_upsert_features",
        "write_features_df_core",
        "write_result",
    }
    violations = [
        (node.lineno, node.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in forbidden_names
    ]

    assert not violations, (
        "arcgis_feature_operations.py is the read/discovery boundary; "
        f"write/publish methods belong in arcgis_feature_writer.py. Violations: {violations}"
    )


def test_arcgis_feature_writer_does_not_import_concrete_client() -> None:
    """The write boundary must depend on provider protocols, not ArcGISClient."""
    tree = _parse_writer()
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
        "arcgis_feature_writer.py must not import ArcGISClient; inject "
        f"app.shared.connections.arcgis_provider protocols instead. Violations: {violations}"
    )


def test_arcgis_client_no_longer_defines_feature_write_methods_directly() -> None:
    """Low-level ArcGIS writes live in arcgis_feature_write_methods.py."""
    tree = _parse_client()
    client_class = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "ArcGISClient"
    )
    moved_methods = {
        "_write_batch",
        "add_features",
        "add_layer_to_service",
        "apply_edits",
        "delete_features",
        "update_features",
    }
    violations = [
        (node.lineno, node.name)
        for node in client_class.body
        if isinstance(node, ast.FunctionDef) and node.name in moved_methods
    ]

    assert not violations, (
        "ArcGISClient must inherit feature write methods from "
        "arcgis_feature_write_methods.py instead of defining them directly. "
        f"Violations: {violations}"
    )


def test_arcgis_feature_write_methods_does_not_import_concrete_client() -> None:
    """The write-method mixin must not import the concrete ArcGISClient."""
    tree = _parse_write_methods()
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
        "arcgis_feature_write_methods.py must not import ArcGISClient; "
        f"inherit the mixin from ArcGISClient instead. Violations: {violations}"
    )
