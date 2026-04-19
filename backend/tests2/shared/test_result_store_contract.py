from pathlib import Path
from types import SimpleNamespace

import pytest

from app.utils.result_store import ResultSaveError, save_result_blob


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def test_save_result_blob_removes_temp_file_only_after_success(monkeypatch, tmp_path):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    result_file = temp_dir / "result.csv"
    result_file.write_text("id\n1\n", encoding="utf-8")

    saved = {}

    import app.db.db_service as db_service
    import app.utils.result_store as result_store

    monkeypatch.setattr(
        result_store,
        "uuid",
        SimpleNamespace(uuid4=lambda: "res-1"),
    )
    monkeypatch.setattr(
        db_service,
        "get_project",
        lambda project_id: {"id": project_id, "group_id": "grp-1"},
    )
    monkeypatch.setattr(
        db_service,
        "save_result",
        lambda **kwargs: saved.update(kwargs),
    )
    monkeypatch.setattr(
        result_store,
        "_apply_results_retention",
        lambda project_id: None,
    )
    monkeypatch.setattr(
        result_store,
        "settings",
        None,
        raising=False,
    )

    result_id = save_result_blob("proj-1", result_file, "csv", rows_count=1)

    assert result_id == "res-1"
    assert saved["project_id"] == "proj-1"
    assert saved["group_id"] == "grp-1"
    assert not result_file.exists()
    assert not temp_dir.exists()


def test_save_result_blob_keeps_temp_file_when_db_save_fails(monkeypatch, tmp_path):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    result_file = temp_dir / "result.csv"
    result_file.write_text("id\n1\n", encoding="utf-8")

    import app.db.db_service as db_service
    import app.utils.result_store as result_store

    monkeypatch.setattr(
        result_store,
        "uuid",
        SimpleNamespace(uuid4=lambda: "res-1"),
    )
    monkeypatch.setattr(
        db_service,
        "get_project",
        lambda project_id: {"id": project_id, "group_id": "grp-1"},
    )
    monkeypatch.setattr(
        db_service,
        "save_result",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    with pytest.raises(ResultSaveError, match="Salvataggio risultato nel DB fallito"):
        save_result_blob("proj-1", result_file, "csv", rows_count=1)

    assert result_file.exists()
    assert temp_dir.exists()
