"""
Tests the artifact resolution behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.scheduler.execution_manager import ExecutionManager


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract, pytest.mark.regression]


def _make_manager():
    manager = ExecutionManager.__new__(ExecutionManager)
    manager._get_connection_service = lambda: object()
    return manager


def _make_schedule_config():
    dest = SimpleNamespace(
        write_mode="append",
        connection_id="conn-1",
        table_name="dest_table",
        schema_name="public",
        match_columns=[],
        batch_size=1000,
    )
    return SimpleNamespace(
        pipeline=SimpleNamespace(project_id="proj-1"),
        destination=dest,
    )


def _make_write_result():
    return SimpleNamespace(
        status="success",
        rows_written=10,
        rows_inserted=10,
        rows_updated=0,
        rows_skipped=0,
        total_rows=10,
        message="ok",
        error_message=None,
    )


def test_execute_carica_prefers_result_id_before_output_path():
    manager = _make_manager()
    config = _make_schedule_config()
    loaded = []

    def _load_dataframe(path):
        loaded.append(str(path))
        return SimpleNamespace(empty=False)

    from app.core.results.db import ResultMetadataRow

    with patch("app.core.dataframes.frame_io.load_dataframe_from_file", side_effect=_load_dataframe), \
         patch("app.core.results.db.get_result_row", return_value=ResultMetadataRow(id="res-1", project_id="proj-1", group_id="grp-1", format="csv")), \
         patch("app.db.pg_connection.fetchone", return_value={"data": b"id\n1\n", "storage_key": None, "project_id": "proj-1"}), \
         patch("app.core.carica.executor.CaricaExecutor") as executor_cls, \
         patch("pathlib.Path.exists", return_value=True):
        executor_cls.return_value.execute.return_value = _make_write_result()
        result = manager._execute_carica(
            config,
            {"result_id": "res-1", "output_path": "C:/tmp/legacy.csv"},
        )

    assert result["success"] is True
    assert len(loaded) == 1
    assert "legacy.csv" not in loaded[0]


def test_execute_carica_rejects_result_id_from_other_project_even_if_output_path_exists():
    manager = _make_manager()
    config = _make_schedule_config()

    # Policy allineata a orchestrator/download: result_id esplicito ma
    # mismatched rispetto al project_id => hard-fail, nessun fallback
    # silente a output_path legacy.
    from app.core.results.db import ResultMetadataRow

    with patch("app.core.dataframes.frame_io.load_dataframe_from_file") as load_dataframe, \
         patch("app.core.results.db.get_result_row", return_value=ResultMetadataRow(id="res-1", project_id="proj-2", group_id="grp-1", format="csv")), \
         patch("app.core.results.blob.get_result_data", return_value=b"id\n1\n"), \
         patch("app.core.carica.executor.CaricaExecutor") as executor_cls, \
         patch("pathlib.Path.exists", return_value=True):
        executor_cls.return_value.execute.return_value = _make_write_result()
        result = manager._execute_carica(
            config,
            {"result_id": "res-1", "output_path": "C:/tmp/fallback.csv"},
        )

    assert result["success"] is False
    assert "non appartiene" in result["error"]
    load_dataframe.assert_not_called()
