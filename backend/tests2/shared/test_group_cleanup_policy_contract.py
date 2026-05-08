"""Contracts for group lifecycle cleanup ownership."""

from __future__ import annotations

import ast
from pathlib import Path


_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_APP = _BACKEND_ROOT / "app"
_DOMAIN_CLEANUPS = (
    _APP / "core" / "arricchisci" / "cleanup.py",
    _APP / "core" / "connections" / "cleanup.py",
    _APP / "core" / "files" / "cleanup.py",
    _APP / "core" / "datasources" / "cleanup.py",
    _APP / "core" / "auth" / "user_cleanup.py",
    _APP / "core" / "scheduler" / "cleanup.py",
)
_KNOWN_GROUP_TABLES = (
    "api_connections",
    "connections",
    "datasources",
    "file_sources",
    "log_runs",
    "schedules",
    "users",
)


def _imports_module(tree: ast.AST, module_name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == module_name for alias in node.names):
                return True
        if isinstance(node, ast.ImportFrom) and node.module == module_name:
            return True
    return False


def test_no_central_group_cleanup_god_helper_exists():
    assert not (_APP / "db" / "group_cleanup.py").exists()


def test_group_cleanup_remains_domain_owned_and_registry_driven():
    registry_path = _APP / "core" / "auth" / "cleanup_registry.py"
    registry_src = registry_path.read_text(encoding="utf-8")
    registry_tree = ast.parse(registry_src)

    assert not _imports_module(registry_tree, "app.db.cleanup_sql")
    for cleanup_path in _DOMAIN_CLEANUPS:
        module_name = cleanup_path.relative_to(_BACKEND_ROOT).with_suffix("")
        dotted = ".".join(module_name.parts)
        assert dotted in registry_src


def test_domain_cleanup_modules_own_static_sql_and_use_low_level_primitive():
    for cleanup_path in _DOMAIN_CLEANUPS:
        src = cleanup_path.read_text(encoding="utf-8")
        tree = ast.parse(src)

        assert _imports_module(tree, "app.db.cleanup_sql")
        assert "delete_group_rows(" in src
        assert "app.db.pg_connection" not in src

        delete_literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("DELETE FROM ")
        ]
        assert delete_literals, f"{cleanup_path} must own its static DELETE SQL"
        assert all("WHERE group_id = ?" in sql for sql in delete_literals)


def test_low_level_cleanup_primitive_does_not_know_domain_tables():
    helper_path = _APP / "db" / "cleanup_sql.py"
    src = helper_path.read_text(encoding="utf-8")

    for table_name in _KNOWN_GROUP_TABLES:
        assert table_name not in src
    assert "format(" not in src
    assert "f\"" not in src
    assert "f'" not in src
