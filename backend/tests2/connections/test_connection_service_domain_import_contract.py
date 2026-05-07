"""Connection service domain ownership contracts."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


def test_connection_service_canonical_import_path_is_domain_owned() -> None:
    from app.core.connections.service import ConnectionService

    assert ConnectionService.__module__ == "app.core.connections.service"


def test_legacy_connection_service_path_is_keep_compat_alias() -> None:
    canonical = importlib.import_module("app.core.connections.service")
    legacy = importlib.import_module("app.services.connection_service")

    assert legacy is canonical


def test_connection_service_uses_domain_db_bridge_not_legacy_config_leaf() -> None:
    source_path = _BACKEND_ROOT / "app" / "core" / "connections" / "service.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert "app.db.db_service" not in imported_modules
    assert "app.db.config.connections" not in imported_modules
    assert "app.db.pg_connection" not in imported_modules
    assert "app.core.connections" in imported_modules


def test_connection_db_bridge_delegates_to_config_leaf() -> None:
    source_path = _BACKEND_ROOT / "app" / "core" / "connections" / "db.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert "app.db.config.connections" in imported_modules
    assert "app.db.db_service" not in imported_modules
