"""Result blob boundary reads object-storage references only."""

from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_get_result_data_reads_object_storage(monkeypatch):
    from app.core.results import blob

    captured = {}

    def fetch(sql, params=()):
        captured.update(sql=sql, params=params)
        return {"storage_key": "results/p/r.bin", "project_id": "p"}

    monkeypatch.setattr("app.db.pg_connection.fetchone", fetch)
    with patch(
        "app.core.resources.loaders.result_blob._download_object_storage",
        return_value=b"abc",
    ):
        assert blob.get_result_data("r", group_id="g") == b"abc"

    assert captured["params"] == ("r", "g")
    assert "data" not in captured["sql"].lower()


def test_get_result_data_rejects_missing_reference(monkeypatch):
    from app.core.results import blob

    monkeypatch.setattr(
        "app.db.pg_connection.fetchone",
        lambda sql, params=(): {"storage_key": None, "project_id": "p"},
    )
    with pytest.raises(RuntimeError, match="senza storage_key"):
        blob.get_result_data("r")
