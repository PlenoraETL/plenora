"""Contract tests for the canonical result metadata DB bridge."""

from __future__ import annotations

import pytest

from app.core.results import db as results_db


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_result_metadata_bridge_delegates_to_legacy_sql_service(monkeypatch):
    import app.db.results_db_service as legacy_results_db

    calls: list[tuple[str, tuple, dict]] = []

    monkeypatch.setattr(
        legacy_results_db,
        "save_result",
        lambda *args, **kwargs: calls.append(("save_result", args, kwargs))
        or {"id": kwargs["result_id"]},
    )
    monkeypatch.setattr(
        legacy_results_db,
        "get_result",
        lambda *args, **kwargs: calls.append(("get_result", args, kwargs))
        or {"id": args[0]},
    )
    monkeypatch.setattr(
        legacy_results_db,
        "get_latest_result",
        lambda *args, **kwargs: calls.append(("get_latest_result", args, kwargs))
        or {"id": "latest"},
    )
    monkeypatch.setattr(
        legacy_results_db,
        "list_results",
        lambda *args, **kwargs: calls.append(("list_results", args, kwargs))
        or [{"id": "latest"}],
    )

    assert results_db.save_result(
        result_id="res-1",
        project_id="proj-1",
        group_id="grp-1",
        filename="out.csv",
        fmt="csv",
        data=b"id\n1\n",
        rows_count=1,
    ) == {"id": "res-1"}
    assert results_db.get_result("res-1") == {"id": "res-1"}
    assert results_db.get_latest_result("proj-1", group_id="grp-1") == {
        "id": "latest"
    }
    assert results_db.list_results("proj-1") == [{"id": "latest"}]

    assert [name for name, _args, _kwargs in calls] == [
        "save_result",
        "get_result",
        "get_latest_result",
        "list_results",
    ]
    assert calls[0][2]["storage_key"] is None
    assert calls[2][1] == ("proj-1", "grp-1")
    assert calls[2][2] == {}
