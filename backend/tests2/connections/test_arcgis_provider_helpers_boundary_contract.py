"""Contracts for generic ArcGIS helper boundaries under connections."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _imports(rel_path: str) -> set[str]:
    tree = ast.parse((_BACKEND_ROOT / rel_path).read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_arcgis_sql_helpers_legacy_path_reexports_connections_boundary() -> None:
    from app.core.connections.arcgis_sql_helpers import (
        chunked,
        query_oid_by_key,
        quote_sql_value,
    )
    from app.core.features import arcgis_sql_helpers as legacy

    assert legacy.chunked is chunked
    assert legacy.query_oid_by_key is query_oid_by_key
    assert legacy.quote_sql_value is quote_sql_value


def test_arcgis_publish_modes_legacy_path_reexports_connections_boundary() -> None:
    from app.core.connections.arcgis_publish_modes import (
        ArcGISPublishModeLayerResult,
        ArcGISPublishModeProviderProtocol,
        publish_existing_layer_mode,
    )
    from app.core.features import arcgis_publish_modes as legacy

    assert legacy.ArcGISPublishModeLayerResult is ArcGISPublishModeLayerResult
    assert legacy.ArcGISPublishModeProviderProtocol is ArcGISPublishModeProviderProtocol
    assert legacy.publish_existing_layer_mode is publish_existing_layer_mode


def test_imdf_arcgis_wrappers_import_generic_helpers_from_connections() -> None:
    sql_imports = _imports("app/core/imdf/arcgis/sql_helpers.py")
    publish_imports = _imports("app/core/imdf/arcgis/publish_ops.py")

    assert "app.core.connections.arcgis_sql_helpers" in sql_imports
    assert "app.core.connections.arcgis_publish_modes" in publish_imports
    assert "app.core.features" not in sql_imports
    assert "app.core.features.arcgis_publish_modes" not in publish_imports
