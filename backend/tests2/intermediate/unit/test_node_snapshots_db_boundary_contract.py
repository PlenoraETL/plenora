"""Contracts for the intermediate node snapshot persistence split."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_INTERMEDIATE_ROOT = _BACKEND_ROOT / "app" / "core" / "intermediate"
_NODE_TABLE_SQL_MARKERS = (
    "INSERT INTO node_meta",
    "SELECT * FROM node_meta",
    "DELETE FROM node_rows",
    "INSERT INTO node_rows",
    "SELECT row_hash, row_data FROM node_rows",
)
_NODE_FACADE_FUNCTIONS = {
    "upsert_node_meta",
    "upsert_node_meta_extended",
    "write_node_snapshot_atomic",
    "get_node_meta",
    "list_node_meta",
    "insert_node_rows",
    "write_node_atomic",
    "get_node_table_rows",
}
_DB_BOUNDARY_CALLS = {
    "execute",
    "fetchall",
    "fetchone",
    "executemany",
    "transaction",
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _string_literals(tree: ast.AST) -> list[str]:
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def test_node_snapshots_db_owns_node_meta_rows_and_json_sentinels() -> None:
    path = _INTERMEDIATE_ROOT / "node_snapshots_db.py"
    tree = _tree(path)

    exported_functions = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    assert {
        "upsert_node_meta",
        "upsert_node_meta_extended",
        "write_node_snapshot_atomic",
        "get_node_meta",
        "list_node_meta",
        "_to_jsonable",
        "_from_jsonable_value",
        "insert_node_rows",
        "write_node_atomic",
        "get_node_table_rows",
    }.issubset(exported_functions)

    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not {
        module
        for module in imported_modules
        if module.startswith("app.core.") and module.endswith(".db")
    }


def test_intermediate_db_facade_contains_no_embedded_node_snapshot_sql() -> None:
    facade_tree = _tree(_INTERMEDIATE_ROOT / "db.py")
    snapshots_tree = _tree(_INTERMEDIATE_ROOT / "node_snapshots_db.py")

    facade_literals = "\n".join(_string_literals(facade_tree))
    snapshots_literals = "\n".join(_string_literals(snapshots_tree))

    leaked = [
        marker for marker in _NODE_TABLE_SQL_MARKERS if marker in facade_literals
    ]
    assert leaked == [], (
        "app.core.intermediate.db must stay a compatibility facade. "
        f"Move node snapshot SQL back to node_snapshots_db.py. Leaked markers: {leaked}"
    )
    missing_from_leaf = [
        marker
        for marker in _NODE_TABLE_SQL_MARKERS
        if marker not in snapshots_literals
    ]
    assert missing_from_leaf == [], (
        "Node snapshot SQL must be owned by node_snapshots_db.py. "
        f"Missing leaf markers: {missing_from_leaf}"
    )


def test_intermediate_db_node_snapshot_facades_only_sync_then_delegate() -> None:
    facade_tree = _tree(_INTERMEDIATE_ROOT / "db.py")
    violations: list[str] = []

    for node in facade_tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name not in _NODE_FACADE_FUNCTIONS:
            continue

        calls = [
            _call_name(call.func)
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
        ]
        direct_boundary_calls = sorted(set(calls) & _DB_BOUNDARY_CALLS)
        if direct_boundary_calls:
            violations.append(
                f"{node.name} calls DB boundary directly: {direct_boundary_calls}"
            )
        if not any(
            isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "_node_snapshots_db"
            and call.func.attr == node.name
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
        ):
            violations.append(f"{node.name} does not delegate to node_snapshots_db")

    assert not violations, (
        "Node snapshot compatibility functions in intermediate/db.py must only "
        "sync patchable boundaries and delegate to node_snapshots_db.py:\n  - "
        + "\n  - ".join(violations)
    )


def test_intermediate_db_node_row_facade_preserves_patchable_batch_insert(
    monkeypatch,
) -> None:
    from app.core.intermediate import db as idb

    calls: list[tuple] = []

    class Cursor:
        def execute(self, sql, params=()):
            calls.append(("execute", sql, tuple(params)))

        def executemany(self, sql, records):
            calls.append(("executemany", sql, list(records)))

    class CursorContext:
        def __enter__(self):
            return Cursor()

        def __exit__(self, exc_type, exc, tb):
            return False

    class Connection:
        def cursor(self):
            return CursorContext()

    class Transaction:
        def __enter__(self):
            return Connection()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(idb, "transaction", lambda: Transaction())

    idb.insert_node_rows(
        "run-1",
        "node-1",
        [{"_row_hash": "h1", "value": 1}],
        {"value": "int"},
    )

    assert calls[0][0] == "execute"
    assert "DELETE FROM node_rows" in calls[0][1]
    assert calls[1][0] == "executemany"
    assert "INSERT INTO node_rows" in calls[1][1]
