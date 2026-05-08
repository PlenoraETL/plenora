"""Guard connections/datasources routers from raw project DB imports."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_ROUTER_PATHS = (
    _BACKEND_ROOT / "app" / "core" / "connections" / "router.py",
    _BACKEND_ROOT / "app" / "core" / "datasources" / "router.py",
)


def _imports_module(path: Path, module_name: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parent_module, _, leaf_name = module_name.rpartition(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module_name:
            return True
        if isinstance(node, ast.ImportFrom) and node.module == parent_module:
            if any(alias.name == leaf_name for alias in node.names):
                return True
        if isinstance(node, ast.Import):
            if any(alias.name == module_name for alias in node.names):
                return True
    return False


def test_connection_and_datasource_routers_use_project_usage_boundary() -> None:
    offenders = [
        path.relative_to(_BACKEND_ROOT).as_posix()
        for path in _ROUTER_PATHS
        if _imports_module(path, "app.core.projects.db")
    ]

    assert not offenders, (
        "Connections/datasources routers must not import app.core.projects.db "
        f"directly; use app.core.projects.usage. Offenders: {offenders}"
    )
