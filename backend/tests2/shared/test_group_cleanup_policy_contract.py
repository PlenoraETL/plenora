"""Contracts for group lifecycle cleanup ownership."""

from __future__ import annotations

import ast
from pathlib import Path

from tests2.shared._ast_identifiers import all_identifiers, calls_method, has_fstring


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

    identifiers = all_identifiers(src)
    assert "_DELETE_GROUP_ROWS_SQL" in identifiers
    assert "delete_group_rows_for_table" in identifiers
    # No dynamic SQL: neither str.format nor f-strings (AST, robust to comments).
    assert not calls_method(src, "format")
    assert not has_fstring(src)

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
    """Epic E: la composizione domain-owned vive nel wiring composition
    root (app/runtime), il meccanismo nel lifecycle neutro."""
    wiring_path = _APP / "runtime" / "group_cleanup_wiring.py"
    wiring_src = wiring_path.read_text(encoding="utf-8")
    wiring_tree = ast.parse(wiring_src)

    assert not _imports_module(wiring_tree, "app.db.cleanup_sql")
    for cleanup_path in _DOMAIN_CLEANUPS:
        module_name = cleanup_path.relative_to(_BACKEND_ROOT).with_suffix("")
        dotted = ".".join(module_name.parts)
        assert dotted in wiring_src


def test_auth_does_not_import_vertical_cleanup_modules():
    """Guardia E5: `auth` non importa i cleanup degli altri domini —
    aggiungere un dominio al cascade delete tocca il wiring composition
    root, mai auth. Unico cleanup ammesso: `auth.user_cleanup` (proprio)."""
    forbidden_prefixes = tuple(
        ".".join(p.relative_to(_BACKEND_ROOT).with_suffix("").parts)
        for p in _DOMAIN_CLEANUPS
        if p.parts[-2] != "auth"
    ) + ("app.core.projects.cleanup",)

    violations: dict[str, list[str]] = {}
    for path in sorted((_APP / "core" / "auth").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offending = sorted(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module in forbidden_prefixes
            # Eccezione censita: il wrapper compatibile re-esporta
            # delete_intermediate_runs_for_group_project via lazy
            # import dal boundary projects.cleanup (storico).
            and not (
                path.name == "cleanup_registry.py"
                and node.module == "app.core.projects.cleanup"
            )
        )
        if offending:
            violations[path.name] = offending

    assert violations == {}, (
        "auth non deve importare i cleanup verticali (Epic E): "
        f"{violations}. La composizione vive in "
        "app/runtime/group_cleanup_wiring.py"
    )


def test_domain_cleanup_modules_keep_public_boundaries_and_use_table_helper():
    for cleanup_path in _DOMAIN_CLEANUPS:
        src = cleanup_path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        identifiers = all_identifiers(src)

        if cleanup_path.parts[-2] == "scheduler":
            assert _imports_module(tree, "app.db.cleanup_sql")
            assert "delete_group_rows" in identifiers
            continue

        if cleanup_path.parts[-2] == "files":
            assert _imports_module(tree, "app.core.files")
            assert "list_file_source_ids_for_group" in identifiers
            assert "delete_file_source" in identifiers
            assert not _imports_module(tree, "app.db.group_cleanup")
        else:
            assert _imports_module(tree, "app.db.group_cleanup")
            assert "delete_group_rows_for_table" in identifiers
        assert not _imports_module(tree, "app.db.pg_connection")

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

    # Table names are SQL string identifiers (live only in string literals),
    # so this stays a raw-source check by design.
    for table_name in _KNOWN_GROUP_TABLES:
        assert table_name not in src
    # No dynamic SQL construction (AST, robust to comments/strings).
    assert not calls_method(src, "format")
    assert not has_fstring(src)
