"""Contract tests for canonical result metadata SQL ownership."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.core.results import db as results_db


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_result_metadata_sql_lives_in_core_results_db(monkeypatch):
    calls: list[tuple[str, str, tuple[object, ...]]] = []

    def _execute(sql: str, params: tuple[object, ...]) -> None:
        calls.append(("execute", sql, params))

    def _fetchone(sql: str, params: tuple[object, ...]) -> dict[str, object]:
        calls.append(("fetchone", sql, params))
        return {"id": params[0]}

    monkeypatch.setattr(results_db, "execute", _execute)
    monkeypatch.setattr(results_db, "fetchone", _fetchone)

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
    assert results_db.save_result(
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
            "FROM results WHERE id = ?"
        ),
        ("res-1",),
    )
    assert calls[2][0] == "execute"
    assert calls[3][0] == "fetchone"


def test_result_metadata_reads_use_core_sql(monkeypatch):
    calls: list[tuple[str, str, tuple[object, ...]]] = []

    def _fetchone(sql: str, params: tuple[object, ...]) -> dict[str, object]:
        calls.append(("fetchone", sql, params))
        return {"id": "latest"}

    def _fetchall(sql: str, params: tuple[object, ...]) -> list[dict[str, object]]:
        calls.append(("fetchall", sql, params))
        return [{"id": "latest"}]

    monkeypatch.setattr(results_db, "fetchone", _fetchone)
    monkeypatch.setattr(results_db, "fetchall", _fetchall)

    assert results_db.get_result("res-1") == {"id": "latest"}
    assert results_db.get_latest_result("proj-1", group_id="grp-1") == {
        "id": "latest"
    }
    assert results_db.get_latest_result("proj-1") == {"id": "latest"}
    assert results_db.list_results("proj-1") == [{"id": "latest"}]

    assert "FROM results WHERE id = ?" in calls[0][1]
    assert calls[0][2] == ("res-1",)
    assert "project_id = ? AND group_id = ?" in calls[1][1]
    assert calls[1][2] == ("proj-1", "grp-1")
    assert "WHERE project_id = ? ORDER BY created_at DESC" in calls[2][1]
    assert calls[2][2] == ("proj-1",)
    assert calls[3][0] == "fetchall"
    assert "FROM results WHERE project_id = ? ORDER BY created_at DESC" in calls[3][1]


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

    monkeypatch.setattr(results_db, "fetchone", _fetchone)

    row = results_db.get_result_row("res-1")

    assert isinstance(row, results_db.ResultMetadataRow)
    assert row.id == "res-1"
    assert row.project_id == "proj-1"
    assert row.group_id == "grp-1"
    assert row.storage_key == "results/proj-1/res-1.bin"
    assert row.to_legacy_dict() == results_db.get_result("res-1")

    with pytest.raises(FrozenInstanceError):
        row.id = "mutated"


def test_results_db_service_retains_metadata_compat_exports():
    import app.db.results_db_service as legacy_results_db

    assert legacy_results_db.save_result is results_db.save_result
    assert legacy_results_db.get_result is results_db.get_result
    assert legacy_results_db.get_latest_result is results_db.get_latest_result
    assert legacy_results_db.list_results is results_db.list_results
