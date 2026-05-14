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


def test_group_cleanup_helper_uses_table_allowlist():
    helper_path = _APP / "db" / "group_cleanup.py"
    src = helper_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    assert "_DELETE_GROUP_ROWS_SQL" in src
    assert "delete_group_rows_for_table" in src
    assert "format(" not in src
    assert "f\"" not in src
    assert "f'" not in src

    delete_literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("DELETE FROM ")
    ]
    assert sorted(delete_literals) == sorted(
        f"DELETE FROM {table_name} WHERE group_id = %s"
        for table_name in _KNOWN_GROUP_TABLES
        if table_name not in {"file_sources", "log_runs", "schedules"}
    )


def test_group_cleanup_remains_domain_owned_and_registry_driven():
    registry_path = _APP / "core" / "auth" / "cleanup_registry.py"
    registry_src = registry_path.read_text(encoding="utf-8")
    registry_tree = ast.parse(registry_src)

    assert not _imports_module(registry_tree, "app.db.cleanup_sql")
    for cleanup_path in _DOMAIN_CLEANUPS:
        module_name = cleanup_path.relative_to(_BACKEND_ROOT).with_suffix("")
        dotted = ".".join(module_name.parts)
        assert dotted in registry_src


def test_domain_cleanup_modules_keep_public_boundaries_and_use_table_helper():
    for cleanup_path in _DOMAIN_CLEANUPS:
        src = cleanup_path.read_text(encoding="utf-8")
        tree = ast.parse(src)

        if cleanup_path.parts[-2] == "scheduler":
            assert _imports_module(tree, "app.db.cleanup_sql")
            assert "delete_group_rows(" in src
            continue

        if cleanup_path.parts[-2] == "files":
            assert _imports_module(tree, "app.core.files")
            assert "list_file_source_ids_for_group(" in src
            assert "delete_file_source(" in src
            assert not _imports_module(tree, "app.db.group_cleanup")
        else:
            assert _imports_module(tree, "app.db.group_cleanup")
            assert "delete_group_rows_for_table(" in src
        assert "app.db.pg_connection" not in src

        delete_literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("DELETE FROM ")
        ]
        assert not delete_literals, f"{cleanup_path} must delegate DELETE SQL to helper"


def test_low_level_cleanup_primitive_does_not_know_domain_tables():
    helper_path = _APP / "db" / "cleanup_sql.py"
    src = helper_path.read_text(encoding="utf-8")

    for table_name in _KNOWN_GROUP_TABLES:
        assert table_name not in src
    assert "format(" not in src
    assert "f\"" not in src
    assert "f'" not in src
