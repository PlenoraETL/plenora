"""PostgreSQL file-source repository stores metadata/reference only."""

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_save_file_source_requires_storage_key(monkeypatch):
    from app.core.files import db

    with pytest.raises(ValueError, match="storage_key"):
        db.save_file_source(
            file_id="f1",
            group_id="g1",
            name="f",
            original_filename="f.csv",
            fmt="csv",
        )


def test_save_file_source_sql_has_no_payload_column(monkeypatch):
    from app.core.files import db

    calls = []
    monkeypatch.setattr(db, "_execute", lambda sql, params: calls.append((sql, params)))
    monkeypatch.setattr(
        db,
        "get_file_source",
        lambda file_id: {"id": file_id, "storage_key": "file-sources/f1/f.csv"},
    )

    row = db.save_file_source(
        file_id="f1",
        group_id="g1",
        name="f",
        original_filename="f.csv",
        fmt="csv",
        storage_key="file-sources/f1/f.csv",
        size_bytes=10,
    )

    sql, params = calls[0]
    projection = sql.split("VALUES", 1)[0].lower()
    assert ", data," not in projection
    assert "storage_key" in projection
    assert b"payload" not in params
    assert row["storage_key"] == "file-sources/f1/f.csv"
