"""Contracts for the intermediate run persistence split."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_INTERMEDIATE_ROOT = _BACKEND_ROOT / "app" / "core" / "intermediate"
_RUN_CRUD_SQL_MARKERS = (
    "INSERT INTO runs",
    "SELECT * FROM runs",
    "UPDATE runs",
)
_RUN_RETENTION_SQL_MARKERS = (
    "DELETE FROM runs",
    "SELECT run_id FROM runs",
)
_RETENTION_POLICY_MARKERS = (
    "DEFAULT_RUNS_RETENTION = 5",
    "retention_n = DEFAULT_RUNS_RETENTION",
    "unpinned[retention_n:]",
    "artifact_status = 'complete'",
)
_INTERMEDIATE_DB_PUBLIC_EXPORTS = {
    "DEFAULT_RUNS_RETENTION",
    "apply_runs_retention",
    "close_connection",
    "create_run",
    "delete_all_runs_for_canvas",
    "get_node_meta",
    "get_node_table_rows",
    "get_run",
    "init_db",
    "insert_node_rows",
    "list_node_meta",
    "list_runs",
    "pin_run",
    "update_run",
    "upsert_node_meta",
    "upsert_node_meta_extended",
    "write_node_atomic",
    "write_node_snapshot_atomic",
}
_INTERMEDIATE_DB_ALLOWED_PUBLIC_FUNCTIONS = (
    _INTERMEDIATE_DB_PUBLIC_EXPORTS - {"DEFAULT_RUNS_RETENTION"}
)
_REMOVED_PRIVATE_COMPAT_ALIASES = {
    "_ALLOWED_RUN_UPDATE_FIELDS",
    "_make_special_float_sentinel",
    "_SPECIAL_FLOAT_TAG_NAN",
    "_SPECIAL_FLOAT_TAG_INF_POS",
    "_SPECIAL_FLOAT_TAG_INF_NEG",
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _string_literals(tree: ast.AST) -> list[str]:
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def _all_assignment_values(tree: ast.AST) -> set[str]:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            continue
        assert isinstance(node.value, (ast.List, ast.Tuple))
        return {
            item.value
            for item in node.value.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        }
    return set()


def _module_assignment_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def test_runs_db_owns_run_crud_without_cross_domain_db_imports() -> None:
    path = _INTERMEDIATE_ROOT / "runs_db.py"
    tree = _tree(path)

    exported_functions = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    assert {
        "create_run",
        "get_run",
        "list_runs",
        "update_run",
        "pin_run",
    }.issubset(exported_functions)

    from app.core.intermediate import runs_db

    assert hasattr(runs_db, "DEFAULT_RUNS_RETENTION")
    assert hasattr(runs_db, "apply_runs_retention")
    assert hasattr(runs_db, "_delete_run")
    assert hasattr(runs_db, "delete_all_runs_for_canvas")

    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not {
        module
        for module in imported_modules
        if module.startswith("app.core.")
        and module.endswith(".db")
        and module != "app.core.intermediate.db"
    }


def test_retention_owns_retention_and_cleanup_logic() -> None:
    retention_tree = _tree(_INTERMEDIATE_ROOT / "retention.py")

    exported_functions = {
        node.name
        for node in ast.walk(retention_tree)
        if isinstance(node, ast.FunctionDef)
    }
    assert {
        "apply_runs_retention",
        "_delete_run",
        "delete_all_runs_for_canvas",
    }.issubset(exported_functions)

    retention_literals = "\n".join(_string_literals(retention_tree))
    missing_from_retention = [
        marker
        for marker in _RUN_RETENTION_SQL_MARKERS
        if marker not in retention_literals
    ]
    assert missing_from_retention == [], (
        "Run retention and cleanup SQL must be owned by retention.py. "
        f"Missing leaf markers: {missing_from_retention}"
    )


def test_intermediate_db_facade_contains_no_embedded_run_sql_or_retention_policy() -> None:
    facade_tree = _tree(_INTERMEDIATE_ROOT / "db.py")
    runs_tree = _tree(_INTERMEDIATE_ROOT / "runs_db.py")
    facade_source = _source(_INTERMEDIATE_ROOT / "db.py")
    retention_source = _source(_INTERMEDIATE_ROOT / "retention.py")

    facade_literals = "\n".join(_string_literals(facade_tree))
    runs_literals = "\n".join(_string_literals(runs_tree))

    leaked = [
        marker
        for marker in (*_RUN_CRUD_SQL_MARKERS, *_RUN_RETENTION_SQL_MARKERS)
        if marker in facade_literals
    ]
    assert leaked == [], (
        "app.core.intermediate.db must stay a compatibility facade. "
        f"Move run SQL back to its leaf module. Leaked markers: {leaked}"
    )
    missing_from_leaf = [
        marker for marker in _RUN_CRUD_SQL_MARKERS if marker not in runs_literals
    ]
    assert missing_from_leaf == [], (
        "Run persistence SQL must be owned by runs_db.py. "
        f"Missing leaf markers: {missing_from_leaf}"
    )
    leaked_policy = [
        marker for marker in _RETENTION_POLICY_MARKERS if marker in facade_source
    ]
    assert leaked_policy == [], (
        "app.core.intermediate.db must not own retention policy. "
        f"Move retention policy to retention.py. Leaked markers: {leaked_policy}"
    )
    missing_policy = [
        marker for marker in _RETENTION_POLICY_MARKERS if marker not in retention_source
    ]
    assert missing_policy == [], (
        "Retention policy and artifact cleanup guardrails must be owned by retention.py. "
        f"Missing markers: {missing_policy}"
    )


def test_runs_db_contains_no_embedded_retention_policy_or_cleanup_sql() -> None:
    runs_tree = _tree(_INTERMEDIATE_ROOT / "runs_db.py")
    runs_source = _source(_INTERMEDIATE_ROOT / "runs_db.py")
    runs_literals = "\n".join(_string_literals(runs_tree))

    leaked_sql = [
        marker for marker in _RUN_RETENTION_SQL_MARKERS if marker in runs_literals
    ]
    leaked_policy = [
        marker for marker in _RETENTION_POLICY_MARKERS if marker in runs_source
    ]
    assert leaked_sql == []
    assert leaked_policy == []


def test_intermediate_db_facade_imports_only_intermediate_leaf_dbs() -> None:
    tree = _tree(_INTERMEDIATE_ROOT / "db.py")

    imports = _imported_modules(tree)

    assert "app.core.projects.db" not in imports
    assert "app.core.intermediate.runs_db" in imports
    assert "app.core.intermediate.node_snapshots_db" in imports
    assert "app.core.intermediate.retention" in imports


def test_intermediate_db_facade_exports_are_explicit_and_ratcheted() -> None:
    tree = _tree(_INTERMEDIATE_ROOT / "db.py")

    assert _all_assignment_values(tree) == _INTERMEDIATE_DB_PUBLIC_EXPORTS

    public_functions = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }
    assert public_functions == _INTERMEDIATE_DB_ALLOWED_PUBLIC_FUNCTIONS

    leaked_private_aliases = (
        _module_assignment_names(tree) & _REMOVED_PRIVATE_COMPAT_ALIASES
    )
    assert leaked_private_aliases == set()


def test_intermediate_db_create_run_facade_preserves_patchable_boundary(
    monkeypatch,
) -> None:
    from app.core.intermediate import db as idb

    captured: list[dict] = []

    def fake_execute(sql, params=()):
        captured.append({"sql": sql, "params": tuple(params)})

    monkeypatch.setattr(idb, "execute", fake_execute)
    monkeypatch.setattr(idb, "get_run", lambda run_id: {"run_id": run_id})

    out = idb.create_run(
        "run-1",
        "canvas-1",
        "group-1",
        total_nodes=2,
        user_id="user-1",
    )

    assert out == {"run_id": "run-1"}
    assert len(captured) == 1
    assert "INSERT INTO runs" in captured[0]["sql"]
    assert "user-1" in captured[0]["params"]


def test_intermediate_db_delete_all_runs_facade_uses_patchable_delete(
    monkeypatch,
) -> None:
    from app.core.intermediate import db as idb

    deleted: list[str] = []
    monkeypatch.setattr(
        idb,
        "fetchall",
        lambda sql, params=(): [{"run_id": "r1"}, {"run_id": "r2"}],
    )
    monkeypatch.setattr(idb, "_delete_run", lambda run_id: deleted.append(run_id))
    monkeypatch.setattr(
        idb,
        "logger",
        SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
    )

    assert idb.delete_all_runs_for_canvas("canvas-1") == 2
    assert deleted == ["r1", "r2"]
