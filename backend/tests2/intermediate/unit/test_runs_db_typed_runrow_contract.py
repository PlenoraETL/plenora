"""Contracts for typed intermediate run rows."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path
from typing import get_origin

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_RUNS_DB_PATH = _BACKEND_ROOT / "app" / "core" / "intermediate" / "runs_db.py"


def _record() -> dict:
    return {
        "run_id": "run-1",
        "canvas_id": "canvas-1",
        "group_id": "group-1",
        "timestamp": "2026-05-08T09:00:00+00:00",
        "status": "running",
        "pinned": 1,
        "total_rows_in": 10,
        "total_rows_out": 8,
        "total_rows_dropped": 2,
        "total_nodes": 3,
        "nodes_ok": 2,
        "nodes_error": 1,
        "nodes_skipped": 0,
        "total_exec_time": 1.25,
        "input_hash": "in",
        "output_hash": "out",
        "user_id": "user-1",
        "future_column": "kept",
    }


def test_runrow_is_frozen_dataclass_not_dict_alias() -> None:
    from app.core.intermediate import runs_db

    assert is_dataclass(runs_db.RunRow)
    assert runs_db.RunRow.__dataclass_params__.frozen is True
    assert runs_db.RunRow is not dict
    assert get_origin(runs_db.RunRow) is None


def test_runs_db_source_does_not_alias_runrow_to_dict() -> None:
    tree = ast.parse(_RUNS_DB_PATH.read_text(encoding="utf-8"))
    bad_assignments = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "RunRow"
            for target in node.targets
        ):
            continue
        value = node.value
        if isinstance(value, ast.Name) and value.id in {"Dict", "dict"}:
            bad_assignments.append(node.lineno)

    assert bad_assignments == []
    assert "RunRow = Dict" not in _RUNS_DB_PATH.read_text(encoding="utf-8")


def test_runs_db_hydrates_fetchone_records_to_runrow() -> None:
    from app.core.intermediate import runs_db

    row = runs_db.get_run(
        "run-1",
        fetchone_func=lambda sql, params: _record(),
    )

    assert isinstance(row, runs_db.RunRow)
    assert not isinstance(row, dict)
    assert row.run_id == "run-1"
    assert row.canvas_id == "canvas-1"
    assert row.group_id == "group-1"
    assert row.pinned is True
    assert row["status"] == "running"
    assert row.get("future_column") == "kept"

    public = row.as_dict()
    assert public["pinned"] is True
    assert public["future_column"] == "kept"
    assert public["total_rows_dropped"] == 2

    with pytest.raises(FrozenInstanceError):
        row.status = "completed"
    with pytest.raises(TypeError):
        row._extra["future_column"] = "mutated"


def test_runs_db_preserves_partial_injected_row_shape() -> None:
    from app.core.intermediate import runs_db

    row = runs_db.RunRow.from_record({"run_id": "run-1"})

    assert row.run_id == "run-1"
    assert row.canvas_id is None
    assert row.as_dict() == {"run_id": "run-1"}


def test_runs_db_list_runs_hydrates_each_record() -> None:
    from app.core.intermediate import runs_db

    rows = runs_db.list_runs(
        "canvas-1",
        "group-1",
        fetchall_func=lambda sql, params: [_record(), {**_record(), "run_id": "run-2"}],
    )

    assert [row.run_id for row in rows] == ["run-1", "run-2"]
    assert all(isinstance(row, runs_db.RunRow) for row in rows)


def test_runs_db_runrow_serializes_to_public_dict() -> None:
    from app.core.intermediate import runs_db

    one = runs_db.get_run("run-1", fetchone_func=lambda sql, params: _record())
    many = runs_db.list_runs(
        "canvas-1",
        "group-1",
        fetchall_func=lambda sql, params: [_record()],
    )

    assert isinstance(one, runs_db.RunRow)
    assert one.as_dict()["run_id"] == "run-1"
    assert one.as_dict()["pinned"] is True
    assert [row.as_dict() for row in many] == [one.as_dict()]


def test_intermediate_router_serializes_native_pg_run_timestamp() -> None:
    from app.core.intermediate.router import _run_to_summary

    native_ts = datetime(2026, 5, 8, 9, tzinfo=timezone.utc)
    summary = _run_to_summary({**_record(), "timestamp": native_ts})

    assert summary.timestamp == native_ts.isoformat()
