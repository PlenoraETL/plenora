"""Contract tests for canonical result metadata SQL ownership."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.core.results import db as results_db
from app.core.results import store as results_store


pytestmark = [pytest.mark.unit, pytest.mark.contract]

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_RESULTS_CORE_TYPED_ROW_PATHS = (
    _BACKEND_ROOT / "app" / "core" / "results" / "db.py",
    _BACKEND_ROOT / "app" / "core" / "results" / "store.py",
    _BACKEND_ROOT / "app" / "core" / "results" / "retention.py",
)


class _FakeUnitOfWork:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, *_exc):
        return False


class _FakeResultSession:
    def __init__(self, calls, *, fetchone_row=None, fetchall_rows=None):
        self.calls = calls
        self.fetchone_row = fetchone_row
        self.fetchall_rows = fetchall_rows or []

    def execute(self, sql: str, params: tuple[object, ...]) -> None:
        self.calls.append(("execute", sql, params))

    def fetchone(self, sql: str, params: tuple[object, ...]) -> dict[str, object]:
        self.calls.append(("fetchone", sql, params))
        return self.fetchone_row if self.fetchone_row is not None else {"id": params[0]}

    def fetchall(self, sql: str, params: tuple[object, ...]) -> list[dict[str, object]]:
        self.calls.append(("fetchall", sql, params))
        return list(self.fetchall_rows)


def test_result_metadata_sql_lives_in_core_results_db(monkeypatch):
    calls: list[tuple[str, str, tuple[object, ...]]] = []

    session = _FakeResultSession(calls)
    monkeypatch.setattr(results_db, "unit_of_work", lambda: _FakeUnitOfWork(session))

    saved_row = results_db.save_result_row(
        result_id="res-1",
        project_id="proj-1",
        group_id="grp-1",
        filename="out.csv",
        fmt="csv",
        data=b"id\n1\n",
        rows_count=1,
        created_by="user-1",
    )

    assert isinstance(saved_row, results_db.ResultMetadataRow)
    assert saved_row.id == "res-1"
    assert results_store.save_result(
        result_id="res-1",
        project_id="proj-1",
        group_id="grp-1",
        filename="out.csv",
        fmt="csv",
        data=b"id\n1\n",
        rows_count=1,
        created_by="user-1",
    ) == {"id": "res-1"}

    assert calls[0][0] == "execute"
    assert "INSERT INTO results" in calls[0][1]
    assert "ON CONFLICT (id) DO UPDATE" in calls[0][1]
    assert calls[0][2][0:6] == (
        "res-1",
        "proj-1",
        "grp-1",
        "out.csv",
        "csv",
        b"id\n1\n",
    )
    assert calls[0][2][6] == 5
    assert calls[0][2][7] == 1
    assert calls[0][2][9:] == ("user-1", None)

    assert calls[1] == (
        "fetchone",
        (
            "SELECT id, project_id, group_id, filename, format, size_bytes, "
            "rows_count, created_at, storage_key "
            "FROM results WHERE id = %s"
        ),
        ("res-1",),
    )
    assert calls[2][0] == "execute"
    assert calls[3][0] == "fetchone"


def test_result_metadata_reads_use_core_sql(monkeypatch):
    calls: list[tuple[str, str, tuple[object, ...]]] = []

    session = _FakeResultSession(
        calls,
        fetchone_row={"id": "latest"},
        fetchall_rows=[{"id": "latest"}],
    )
    monkeypatch.setattr(results_db, "unit_of_work", lambda: _FakeUnitOfWork(session))

    result_row = results_db.get_result_row("res-1")
    latest_group_row = results_db.get_latest_result_row("proj-1", group_id="grp-1")
    latest_row = results_db.get_latest_result_row("proj-1")
    result_rows = results_db.list_result_rows("proj-1")

    assert isinstance(result_row, results_db.ResultMetadataRow)
    assert result_row.id == "latest"
    assert isinstance(latest_group_row, results_db.ResultMetadataRow)
    assert latest_group_row.id == "latest"
    assert isinstance(latest_row, results_db.ResultMetadataRow)
    assert latest_row.id == "latest"
    assert result_rows == [results_db.ResultMetadataRow(id="latest", _selected_keys=("id",))]

    assert "FROM results WHERE id = %s" in calls[0][1]
    assert calls[0][2] == ("res-1",)
    assert "project_id = %s AND group_id = %s" in calls[1][1]
    assert calls[1][2] == ("proj-1", "grp-1")
    assert "WHERE project_id = %s ORDER BY created_at DESC" in calls[2][1]
    assert calls[2][2] == ("proj-1",)
    assert calls[3][0] == "fetchall"
    assert "FROM results WHERE project_id = %s ORDER BY created_at DESC" in calls[3][1]


def test_result_metadata_row_is_typed_read_only_boundary(monkeypatch):
    def _fetchone(sql: str, params: tuple[object, ...]) -> dict[str, object]:
        return {
            "id": "res-1",
            "project_id": "proj-1",
            "group_id": "grp-1",
            "filename": "out.csv",
            "format": "csv",
            "size_bytes": 12,
            "rows_count": 2,
            "created_at": "2026-05-08T10:00:00+00:00",
            "storage_key": "results/proj-1/res-1.bin",
        }

    session = _FakeResultSession(calls=[], fetchone_row=_fetchone("", ()))
    monkeypatch.setattr(results_db, "unit_of_work", lambda: _FakeUnitOfWork(session))

    row = results_db.get_result_row("res-1")

    assert isinstance(row, results_db.ResultMetadataRow)
    assert row.id == "res-1"
    assert row.project_id == "proj-1"
    assert row.group_id == "grp-1"
    assert row.storage_key == "results/proj-1/res-1.bin"
    assert not hasattr(row, "to_legacy_dict")
    assert not hasattr(row, "get")

    with pytest.raises(FrozenInstanceError):
        row.id = "mutated"
    with pytest.raises(TypeError):
        row["id"]  # type: ignore[index]


def test_results_metadata_legacy_serialization_is_explicit_store_boundary() -> None:
    row = results_db.ResultMetadataRow(
        id="res-1",
        project_id="proj-1",
        group_id="grp-1",
        filename="out.csv",
        format="csv",
        size_bytes=12,
        rows_count=2,
        created_at="2026-05-08T10:00:00+00:00",
        storage_key="results/proj-1/res-1.bin",
    )

    assert results_store._metadata_public_payload(row) == {
        "id": "res-1",
        "project_id": "proj-1",
        "group_id": "grp-1",
        "filename": "out.csv",
        "format": "csv",
        "size_bytes": 12,
        "rows_count": 2,
        "created_at": "2026-05-08T10:00:00+00:00",
        "storage_key": "results/proj-1/res-1.bin",
    }


def test_core_results_metadata_rows_are_not_dict_accessed_after_hydration() -> None:
    """Once result metadata is typed, core results code must use attributes."""
    offenders: list[str] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self, rel: str) -> None:
            self.rel = rel
            self.scope: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        def _in_hydration_boundary(self) -> bool:
            return self.scope == ["ResultMetadataRow", "from_mapping"]

        def _flag_row_access(self, node: ast.AST, kind: str) -> None:
            if self._in_hydration_boundary():
                return
            scope = ".".join(self.scope) or "<module>"
            offenders.append(f"{self.rel}:{node.lineno}:{scope}:{kind}")

        def visit_Subscript(self, node: ast.Subscript) -> None:
            if isinstance(node.value, ast.Name) and node.value.id == "row":
                self._flag_row_access(node, "row[...]")
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "row"
            ):
                self._flag_row_access(node, "row.get(...)")
            self.generic_visit(node)

    for path in _RESULTS_CORE_TYPED_ROW_PATHS:
        rel = path.relative_to(_BACKEND_ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        Visitor(rel).visit(ast.parse(source))

    assert offenders == []


def test_result_metadata_exports_live_only_at_canonical_boundary():
    assert results_db.save_result_row.__module__ == "app.core.results.db"
    assert results_db.get_result_row.__module__ == "app.core.results.db"
    assert results_db.get_latest_result_row.__module__ == "app.core.results.db"
    assert results_db.list_result_rows.__module__ == "app.core.results.db"
    assert not hasattr(results_db, "save_result")
    assert not hasattr(results_db, "get_result")
    assert not hasattr(results_db, "get_latest_result")
    assert not hasattr(results_db, "list_results")
