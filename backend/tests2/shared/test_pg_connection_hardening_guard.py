"""DB hardening guards for the canonical PostgreSQL boundary.

These tests are intentionally source-bound.  They protect the next
cleanup wave from reviving SQLite-style ``?`` placeholders at the
``app.db.pg_connection`` boundary and from making ``pg_connection``
perform work during import.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.shared]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_ROOT = _BACKEND_ROOT / "app"
_PG_CONNECTION = _APP_ROOT / "db" / "pg_connection.py"

_PG_HELPERS = {"fetchone", "fetchall", "execute", "executemany"}
_PG_CONNECTION_HELPERS = {"get_connection", "transaction"}
_NON_PRODUCTIVE_ROOT_PARTS = {"tests2", "migrations"}


def _rel(path: Path) -> str:
    return path.relative_to(_BACKEND_ROOT).as_posix()


def _iter_productive_python() -> Iterable[Path]:
    for path in sorted(_APP_ROOT.rglob("*.py")):
        if any(part in _NON_PRODUCTIVE_ROOT_PARTS for part in path.parts):
            continue
        yield path


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8-sig"))


def _expr_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _expr_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _sql_literal_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _sql_literal_text(node.left)
        right = _sql_literal_text(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _contains_legacy_placeholder(sql: str) -> bool:
    return "?" in sql


class _PgSqlPlaceholderVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.module_aliases: set[str] = set()
        self.helper_aliases: set[str] = set()
        self.connection_helper_aliases: set[str] = set()
        self.pg_conn_vars: set[str] = set()
        self.pg_cursor_vars: set[str] = set()
        self.sql_vars: dict[str, str] = {}
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "app.db.pg_connection":
                self.module_aliases.add(alias.asname or "pg_connection")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "app.db":
            for alias in node.names:
                if alias.name == "pg_connection":
                    self.module_aliases.add(alias.asname or alias.name)
        elif node.module == "app.db.pg_connection":
            for alias in node.names:
                local_name = alias.asname or alias.name
                if alias.name in _PG_HELPERS:
                    self.helper_aliases.add(local_name)
                if alias.name in _PG_CONNECTION_HELPERS:
                    self.connection_helper_aliases.add(local_name)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        sql_text = _sql_literal_text(node.value)
        if sql_text is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.sql_vars[target.id] = sql_text
            if _looks_like_sql(sql_text) and _contains_legacy_placeholder(sql_text):
                self.violations.append(
                    f"{_rel(self.path)}:{node.lineno}:sql-literal::{sql_text.strip()[:120]}"
                )

        if isinstance(node.value, ast.Call):
            call_name = _expr_name(node.value.func)
            if call_name in self.connection_helper_aliases:
                self._record_name_targets(node.targets, self.pg_conn_vars)
            elif (
                isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "cursor"
                and _expr_name(node.value.func.value) in self.pg_conn_vars
            ):
                self._record_name_targets(node.targets, self.pg_cursor_vars)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        added_conns: set[str] = set()
        added_cursors: set[str] = set()

        for item in node.items:
            ctx = item.context_expr
            optional = item.optional_vars
            if not isinstance(optional, ast.Name):
                continue

            if isinstance(ctx, ast.Call):
                call_name = _expr_name(ctx.func)
                if call_name in self.connection_helper_aliases:
                    self.pg_conn_vars.add(optional.id)
                    added_conns.add(optional.id)
                elif (
                    isinstance(ctx.func, ast.Attribute)
                    and ctx.func.attr == "cursor"
                    and _expr_name(ctx.func.value) in self.pg_conn_vars
                ):
                    self.pg_cursor_vars.add(optional.id)
                    added_cursors.add(optional.id)

        for stmt in node.body:
            self.visit(stmt)

        self.pg_conn_vars.difference_update(added_conns)
        self.pg_cursor_vars.difference_update(added_cursors)

    def visit_Call(self, node: ast.Call) -> None:
        if node.args:
            call_name = _expr_name(node.func)
            sql_text = _sql_literal_text(node.args[0])
            if sql_text is None and isinstance(node.args[0], ast.Name):
                sql_text = self.sql_vars.get(node.args[0].id)
            if sql_text and _contains_legacy_placeholder(sql_text):
                if self._is_pg_helper_call(node, call_name) or _looks_like_sql(sql_text):
                    self.violations.append(
                        f"{_rel(self.path)}:{node.lineno}:{call_name}::{sql_text.strip()[:120]}"
                    )

        self.generic_visit(node)

    def _is_pg_helper_call(self, node: ast.Call, call_name: str | None) -> bool:
        if call_name in self.helper_aliases:
            return True
        if isinstance(node.func, ast.Attribute):
            owner = _expr_name(node.func.value)
            if owner in self.module_aliases and node.func.attr in _PG_HELPERS:
                return True
            if owner in self.pg_cursor_vars and node.func.attr in {"execute", "executemany"}:
                return True
        return False

    @staticmethod
    def _record_name_targets(targets: list[ast.expr], out: set[str]) -> None:
        for target in targets:
            if isinstance(target, ast.Name):
                out.add(target.id)


def _scan_pg_legacy_placeholder_calls() -> list[str]:
    violations: list[str] = []
    for path in _iter_productive_python():
        tree = _parse(path)
        visitor = _PgSqlPlaceholderVisitor(path)
        visitor.visit(tree)
        violations.extend(visitor.violations)
    return sorted(violations)


def _looks_like_sql(text: str) -> bool:
    upper = " ".join(text.upper().split())
    return any(
        marker in upper
        for marker in (
            "SELECT ",
            "INSERT INTO ",
            "UPDATE ",
            "DELETE FROM ",
            " WHERE ",
            " LIMIT ",
        )
    )


def _top_level_calls(tree: ast.Module, qualified_name: str) -> list[int]:
    lines: list[int] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and _expr_name(child.func) == qualified_name:
                lines.append(child.lineno)
    return lines


def test_pg_connection_callers_use_native_psycopg_placeholders():
    """PostgreSQL callsites must pass native ``%s`` placeholders.

    The old pg helper adapter used to convert ``?`` to ``%s`` globally.
    That is unsafe because ``?`` can be a real SQL literal/operator.  New
    productive callsites must write PostgreSQL-native SQL directly.
    """

    violations = _scan_pg_legacy_placeholder_calls()

    assert not violations, (
        "Productive code still passes SQLite-style '?' placeholders into "
        "app.db.pg_connection helpers/cursors. Migrate these SQL literals "
        "to psycopg2-native '%s' placeholders; do not revive automatic "
        "global replacement.\n"
        + "\n".join(f"  - {item}" for item in violations)
    )


def test_pg_connection_import_is_passive_for_database_url_and_pool():
    """Importing ``app.db.pg_connection`` must not validate or open the DB."""

    tree = _parse(_PG_CONNECTION)
    top_level_raises = [
        node.lineno
        for node in tree.body
        if isinstance(node, (ast.Raise, ast.If))
        and any(isinstance(child, ast.Raise) for child in ast.walk(node))
    ]
    top_level_pool_calls = _top_level_calls(
        tree,
        "psycopg2.pool.ThreadedConnectionPool",
    )

    assert not top_level_raises, (
        "pg_connection import must be passive: do not raise at module load "
        "when DATABASE_URL is absent. Move validation into bootstrap or lazy "
        f"pool initialization. Top-level raise blocks at lines: {top_level_raises}"
    )
    assert not top_level_pool_calls, (
        "pg_connection must not instantiate ThreadedConnectionPool at import. "
        f"Top-level pool calls: {top_level_pool_calls}"
    )


def test_adapt_params_cannot_global_replace_question_placeholders():
    """The legacy adapter may be removed or fail closed, never replace all ``?``."""

    tree = _parse(_PG_CONNECTION)
    source = _PG_CONNECTION.read_text(encoding="utf-8-sig")
    replace_calls: list[int] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "replace":
            continue
        if len(node.args) < 2:
            continue
        first = _sql_literal_text(node.args[0])
        second = _sql_literal_text(node.args[1])
        if first == "?" and second == "%s":
            replace_calls.append(node.lineno)

    assert not replace_calls, (
        "_adapt_params or equivalent code must not perform global "
        "sql.replace('?', '%s'). Remove the adapter or make it fail closed "
        f"on legacy placeholders. Replace calls at lines: {replace_calls}"
    )
    # RAW: expression-pattern - checking .replace("?", "%s") with specific literal args; no AST helper covers positional-arg values on method calls
    assert ".replace(\"?\", \"%s\")" not in source and ".replace('?', '%s')" not in source
