import pytest

from app.db import results_db_service


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def test_get_result_data_converts_memoryview_to_bytes(monkeypatch):
    monkeypatch.setattr(
        results_db_service,
        "fetchone",
        lambda sql, params: {"data": memoryview(b"abc")},
    )

    data = results_db_service.get_result_data("res-1")

    assert data == b"abc"


def test_get_result_data_uses_group_filter_when_provided(monkeypatch):
    seen = {}

    def _fetchone(sql, params):
        seen["sql"] = sql
        seen["params"] = params
        return {"data": b"abc"}

    monkeypatch.setattr(results_db_service, "fetchone", _fetchone)

    results_db_service.get_result_data("res-1", group_id="grp-1")

    assert "group_id = ?" in seen["sql"]
    assert seen["params"] == ("res-1", "grp-1")
