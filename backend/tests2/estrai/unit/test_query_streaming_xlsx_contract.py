"""Contracts for cursor-to-XLSX extraction streaming."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from openpyxl import load_workbook

from app.core.query_execution.query_executor import QueryExecutor
from app.core.query_execution.query_streaming import execute_streaming_to_writer
from app.shared.execution.query_results import StreamedQueryResult


pytestmark = [
    pytest.mark.unit,
    pytest.mark.estrai,
    pytest.mark.contract,
    pytest.mark.regression,
]


def test_cursor_rows_stream_directly_to_valid_xlsx():
    result = MagicMock()
    result.cursor.description = [("id",), ("literal",), ("dirty",)]
    result.fetchmany.side_effect = [
        [(1, "=1+1", "prima\r\nriga   "), (2, "testo", None)],
        [],
    ]

    connection_handle = MagicMock()
    connection_handle.execute.return_value = result
    connection_context = MagicMock()
    connection_context.__enter__.return_value = connection_handle
    connection_context.__exit__.return_value = False
    engine = MagicMock()
    engine.connect.return_value = connection_context
    engine_factory = MagicMock()
    engine_factory.create_engine.return_value = engine

    sink = io.BytesIO()
    rows_count, columns = execute_streaming_to_writer(
        engine_factory=engine_factory,
        connection=SimpleNamespace(type="sqlite"),
        sql="SELECT id, literal, dirty FROM source",
        params=None,
        output_writer=sink,
        output_format="xlsx",
    )

    assert rows_count == 2
    assert columns == ["id", "literal", "dirty"]
    assert result.fetchmany.call_count == 2

    workbook = load_workbook(io.BytesIO(sink.getvalue()), data_only=False)
    sheet = workbook.active
    assert [cell.value for cell in sheet[1]] == columns
    assert sheet["A2"].value == 1
    assert sheet["B2"].value == "=1+1"
    assert sheet["B2"].data_type == "s"
    assert sheet["C2"].value == "prima riga"
    assert sheet["C3"].value is None


def test_query_executor_routes_xlsx_to_streaming_persistence():
    persistence = MagicMock()
    persistence.persist_streaming_query_to_storage.return_value = StreamedQueryResult(
        result_id="result-xlsx",
        rows_count=42,
        columns=["id", "name"],
        output_path=Path("report.xlsx"),
        storage_key="groups/g/results/report.xlsx",
    )
    executor = QueryExecutor(
        engine_factory=MagicMock(),
        logger=MagicMock(),
        result_persistence=persistence,
    )
    executor.execute_raw = MagicMock(
        side_effect=AssertionError("XLSX streaming non deve materializzare execute_raw")
    )

    result = executor.execute_query(
        connection=SimpleNamespace(type="sqlite"),
        sql="SELECT id, name FROM source",
        project_id="project-1",
        output_filename="report",
        output_format="xlsx",
        group_id="group-1",
    )

    assert result["success"] is True
    assert result["rows_count"] == 42
    assert result["result_id"] == "result-xlsx"
    assert result["storage_key"] == "groups/g/results/report.xlsx"
    executor.execute_raw.assert_not_called()
    persistence.persist_dataframe_result.assert_not_called()
    persistence.persist_streaming_query_to_storage.assert_called_once()
    assert (
        persistence.persist_streaming_query_to_storage.call_args.kwargs[
            "output_filename"
        ]
        == "report.xlsx"
    )


def test_xlsx_stream_fails_before_exceeding_excel_row_limit(monkeypatch):
    from app.core.dataframes import export_writer

    monkeypatch.setattr(export_writer, "_XLSX_MAX_ROWS", 3)

    with pytest.raises(ValueError, match="al massimo 2 righe dati"):
        export_writer.write_rows_to_xlsx_stream(
            [(1,), (2,), (3,)],
            ["id"],
            io.BytesIO(),
        )
