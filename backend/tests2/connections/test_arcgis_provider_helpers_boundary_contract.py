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


def test_arcgis_feature_helper_legacy_paths_are_removed() -> None:
    assert not (_BACKEND_ROOT / "app/core/features/arcgis_sql_helpers.py").exists()
    assert not (_BACKEND_ROOT / "app/core/features/arcgis_publish_modes.py").exists()


# (Il subpackage `imdf/arcgis/` e' stato rimosso col cutover: la policy IMDF
# usa direttamente i boundary `connections.arcgis_*` + il writer condiviso.)
