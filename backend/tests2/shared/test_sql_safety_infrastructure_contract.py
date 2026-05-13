from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP = _BACKEND_ROOT / "app"
_SQL_SAFETY_DOTTED = "app.db.sql_safety"
_LEGACY_SAFE_SET_DOTTED = "app.db.config.safe_set"
_SQL_KEYWORDS = (
    "SELECT ",
    "INSERT ",
    "UPDATE ",
    "DELETE ",
    "CREATE ",
    "DROP ",
    "ALTER ",
)
_SQL_CALLS = {
    "execute",
    "execute_boundary",
    "executemany",
    "fetchall",
    "fetchone",
    "_pg_fetchone",
    "_pg_fetchall",
}
_GUARDED_CORE_SQL_FILES = {
    "app/core/auth/shares.py",
    "app/core/auth/visibility.py",
    "app/core/features/features_service.py",
    "app/core/orchestrator/execution_logs.py",
    "app/core/scheduler/log_service.py",
}
_ALLOWED_DYNAMIC_SQL_FSTRINGS = {
    (
        "app/core/auth/db.py",
        "update_user",
        "UPDATE users SET ",
    ): "Dynamic SET clause comes from safe_set_clause('users', fields).",
    (
        "app/core/connections/db.py",
        "update_connection",
        "UPDATE connections SET ",
    ): "Dynamic SET clause comes from safe_set_clause('connections', fields).",
    (
        "app/core/datasources/db.py",
        "update_datasource",
        "UPDATE datasources SET ",
    ): "Dynamic SET clause comes from safe_set_clause('datasources', fields).",
    (
        "app/core/eseguibili/db.py",
        "update_eseguibile",
        "UPDATE eseguibili SET ",
    ): "Dynamic SET clause comes from safe_set_clause('eseguibili', fields).",
    (
        "app/core/files/db.py",
        "get_file_source",
        "SELECT * FROM file_sources WHERE id = %s",
    ): "Optional group filter is a static `AND group_id = %s` fragment.",
    (
        "app/core/files/db.py",
        "get_file_source",
        "SELECT id, group_id, name, original_filename, format, size_bytes, metadata, created_at FROM file_sources WHERE id = %s",
    ): "Optional group filter is a static `AND group_id = %s` fragment.",
    (
        "app/core/intermediate/runs_db.py",
        "update_run",
        "UPDATE runs SET ",
    ): (
        "Dynamic SET identifiers are constrained by "
        "_ALLOWED_RUN_UPDATE_FIELDS before SQL construction."
    ),
    (
        "app/core/projects/db.py",
        "update_project_row",
        "UPDATE projects SET ",
    ): "Dynamic SET clause comes from safe_set_clause('projects', fields).",
    (
        "app/core/projects/db.py",
        "list_owned_project_ids",
        "SELECT id FROM projects WHERE id IN (",
    ): "Dynamic fragment contains only generated parameter placeholders.",
    (
        "app/core/scheduler/db.py",
        "update_schedule",
        "UPDATE schedules SET ",
    ): "Dynamic SET clause comes from safe_set_clause('schedules', fields).",
    (
        "app/core/scheduler/db.py",
        "list_schedule_logs",
        "SELECT log_id, schedule_id, timestamp, level, event, ",
    ): "Dynamic IN(...) fragment contains only generated %s parameter placeholders.",
    (
        "app/core/auth/shares.py",
        "list_resource_shares",
        "SELECT group_id FROM ",
    ): "Share table and foreign key come from visibility._SHAREABLE.",
    (
        "app/core/auth/shares.py",
        "add_resource_share",
        "INSERT INTO ",
    ): "Share table and foreign key come from visibility._SHAREABLE.",
    (
        "app/core/auth/shares.py",
        "remove_resource_share",
        "DELETE FROM ",
    ): "Share table and foreign key come from visibility._SHAREABLE.",
    (
        "app/core/auth/shares.py",
        "set_resource_shares",
        "DELETE FROM ",
    ): "Share table and foreign key come from visibility._SHAREABLE.",
    (
        "app/core/auth/shares.py",
        "set_resource_shares",
        "INSERT INTO ",
    ): "Share table and foreign key come from visibility._SHAREABLE.",
    (
        "app/core/auth/visibility.py",
        "load_shareable_resource_owner",
        "SELECT id, group_id FROM ",
    ): "Resource table is validated against visibility._SHAREABLE.",
    (
        "app/core/auth/visibility.py",
        "visible_resource_ids",
        "SELECT id FROM ",
    ): "Resource and share table identifiers come from visibility._SHAREABLE.",
    (
        "app/core/auth/visibility.py",
        "is_resource_visible",
        "SELECT group_id FROM ",
    ): "Resource table is validated against visibility._SHAREABLE.",
    (
        "app/core/auth/visibility.py",
        "is_resource_visible",
        "SELECT 1 FROM ",
    ): "Share table and foreign key come from visibility._SHAREABLE.",
    (
        "app/core/features/features_service.py",
        "update_connection_db",
        "UPDATE arcgis_connections SET ",
    ): "SET columns come from the local field_map whitelist.",
    (
        "app/core/orchestrator/execution_logs.py",
        "list_logs",
        "SELECT * FROM execution_logs ",
    ): "WHERE fragment is built from static condition templates only.",
    (
        "app/core/scheduler/log_service.py",
        "get_logs",
        "SELECT COUNT(*) AS n FROM plenora.schedule_logs ",
    ): "WHERE fragment is selected from static schedule_id/event filters.",
    (
        "app/core/scheduler/log_service.py",
        "get_logs",
        "SELECT * FROM plenora.schedule_logs ",
    ): "WHERE fragment is selected from static schedule_id/event filters.",
    (
        "app/core/tickets/db.py",
        "update_ticket",
        "UPDATE tickets SET ",
    ): "Dynamic SET identifiers come from the local update_ticket fields whitelist.",
    (
        "app/core/tickets/db.py",
        "count_tickets",
        "SELECT COUNT(*) AS cnt FROM tickets ",
    ): "WHERE fragment is built from static ticket filter templates and generated placeholders.",
    (
        "app/core/tickets/db.py",
        "counts_by_severity",
        "SELECT severity, COUNT(*) AS cnt FROM tickets ",
    ): "WHERE fragment is built from static ticket filter templates and generated placeholders.",
}


def _imports_module(tree: ast.AST, dotted: str) -> bool:
    parent, _, leaf = dotted.rpartition(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == dotted for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == dotted:
                return True
            if node.module == parent:
                if any(alias.name == leaf for alias in node.names):
                    return True
    return False


def _contains_sql(text: str) -> bool:
    upper = text.upper()
    return any(keyword in upper for keyword in _SQL_KEYWORDS)


def _joined_str_text(node: ast.JoinedStr) -> str:
    return "".join(
        part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else "{}"
        for part in node.values
    )


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _nearest_function(parents: dict[ast.AST, ast.AST], node: ast.AST) -> str:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return "<module>"


def _is_guarded_core_sql_path(path: Path) -> bool:
    rel = path.relative_to(_BACKEND_ROOT).as_posix()
    if rel in _GUARDED_CORE_SQL_FILES:
        return True
    if not rel.startswith("app/core/"):
        return False
    return path.name.endswith("db.py") or "cleanup" in path.stem


def _is_allowed_dynamic_sql(rel: str, function: str, sql: str) -> bool:
    normalized_sql = sql.lstrip()
    return any(
        rel == allowed_rel
        and function == allowed_function
        and normalized_sql.startswith(allowed_prefix)
        for (
            allowed_rel,
            allowed_function,
            allowed_prefix,
        ) in _ALLOWED_DYNAMIC_SQL_FSTRINGS
    )


def _dynamic_sql_violations(path: Path) -> list[str]:
    rel = path.relative_to(_BACKEND_ROOT).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "format"
                and isinstance(node.func.value, ast.Constant)
                and isinstance(node.func.value.value, str)
                and _contains_sql(node.func.value.value)
            ):
                violations.append(
                    f"{rel}:{node.lineno}: SQL string uses .format()"
                )

            call_name = _call_name(node.func)
            if (
                call_name in _SQL_CALLS
                and node.args
                and isinstance(node.args[0], ast.JoinedStr)
            ):
                sql = _joined_str_text(node.args[0])
                if _contains_sql(sql):
                    function = _nearest_function(parents, node)
                    if not _is_allowed_dynamic_sql(rel, function, sql):
                        violations.append(
                            f"{rel}:{node.lineno}: SQL execute uses f-string"
                        )

        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Mod)
            and isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)
            and _contains_sql(node.left.value)
        ):
            violations.append(f"{rel}:{node.lineno}: SQL string uses % interpolation")

    return violations


def test_safe_set_clause_is_owned_by_generic_db_infrastructure():
    import app.db.sql_safety as sql_safety

    assert sql_safety.safe_set_clause("users", {"email": "a@example.test"}) == (
        "email = %s",
        ("a@example.test",),
    )
    assert importlib.util.find_spec(_LEGACY_SAFE_SET_DOTTED) is None


def test_production_safe_set_users_do_not_import_config_safe_set():
    violations: list[str] = []
    for path in _APP.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        if _imports_module(tree, _LEGACY_SAFE_SET_DOTTED):
            violations.append(path.relative_to(_BACKEND_ROOT).as_posix())

    assert not violations, (
        "`safe_set_clause` is DB infrastructure and must be imported from "
        f"`{_SQL_SAFETY_DOTTED}`, not `{_LEGACY_SAFE_SET_DOTTED}`.\n"
        "Violations:\n  - " + "\n  - ".join(sorted(violations))
    )


def test_core_db_modules_do_not_build_sql_with_unsafe_interpolation():
    violations: list[str] = []
    for path in (_APP / "core").rglob("*.py"):
        if not _is_guarded_core_sql_path(path):
            continue
        violations.extend(_dynamic_sql_violations(path))

    assert not violations, (
        "Core DB, persistence-helper, and cleanup modules must use "
        "parameterized SQL. Do not build query text with `.format()` or `%` "
        "interpolation. SQL f-strings require a documented allowlist entry "
        "proving identifiers are constrained by a static whitelist.\n"
        "Violations:\n  - " + "\n  - ".join(sorted(violations))
    )
