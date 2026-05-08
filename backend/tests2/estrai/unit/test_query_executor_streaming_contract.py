"""
Contract test C11 — streaming path di QueryExecutor.

Copre i gap rimasti dopo il fix C11:
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.db.query_streaming import execute_streaming_to_file
from app.db.query_executor import QueryExecutor


pytestmark = [pytest.mark.unit, pytest.mark.estrai, pytest.mark.contract, pytest.mark.regression]


def test_query_executor_delegates_streaming_mechanics_to_query_streaming_module():
    source = Path("backend/app/db/query_executor.py").read_text(encoding="utf-8")

    assert "from app.db.query_streaming import execute_streaming_to_file" in source
    assert "csv.writer" not in source
    assert "ParquetWriter" not in source
    assert "pyarrow" not in source


class _FakeResult:
    def __init__(self, columns, chunks):
        self.cursor = MagicMock()
        self.cursor.description = [(c,) for c in columns]
        self._chunks = list(chunks)

    def fetchmany(self, size):
        if self._chunks:
            return self._chunks.pop(0)
        return []


def _make_factory_with_result(fake_result):
    fake_conn = MagicMock()
    fake_conn.execute.return_value = fake_result

    fake_ctx = MagicMock()
    fake_ctx.__enter__.return_value = fake_conn
    fake_ctx.__exit__.return_value = False

    fake_engine = MagicMock()
    fake_engine.connect.return_value = fake_ctx

    factory = MagicMock()
    factory.create_engine.return_value = fake_engine

    return factory


def _make_executor_with_result(fake_result):
    factory = _make_factory_with_result(fake_result)
    return QueryExecutor(engine_factory=factory, logger=MagicMock())


def test_execute_streaming_to_file_writes_csv_chunks(tmp_path, monkeypatch):
    import app.config

    monkeypatch.setattr(app.config.settings, "ESTRAI_MAX_ROWS", 10_000, raising=False)

    fake_result = _FakeResult(
        columns=["id", "name"],
        chunks=[
            [(1, "a"), (2, "b")],
            [(3, "c")],
            [],
        ],
    )
    factory = _make_factory_with_result(fake_result)
    conn = MagicMock()
    conn.type = "postgres"

    out = tmp_path / "out.csv"
    row_count, columns = execute_streaming_to_file(
        engine_factory=factory,
        connection=conn,
        sql="SELECT id, name FROM t",
        params=None,
        output_path=out,
        output_format="csv",
    )

    assert row_count == 3
    assert columns == ["id", "name"]
    assert out.read_text(encoding="utf-8").splitlines() == [
        "id,name",
        "1,a",
        "2,b",
        "3,c",
    ]


def test_execute_streaming_to_file_writes_parquet_chunks(tmp_path, monkeypatch):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    import app.config

    monkeypatch.setattr(app.config.settings, "ESTRAI_MAX_ROWS", 10_000, raising=False)

    fake_result = _FakeResult(
        columns=["id", "name"],
        chunks=[
            [(1, "a"), (2, "b")],
            [(3, "c")],
            [],
        ],
    )
    executor = _make_executor_with_result(fake_result)
    conn = MagicMock()
    conn.type = "postgres"

    out = tmp_path / "out.parquet"
    row_count, columns = executor._execute_streaming_to_file(
        connection=conn,
        sql="SELECT id, name FROM t",
        params=None,
        output_path=out,
        output_format="parquet",
    )

    table = pq.read_table(out)
    rows = table.to_pylist()

    assert row_count == 3
    assert columns == ["id", "name"]
    assert rows == [
        {"id": 1, "name": "a"},
        {"id": 2, "name": "b"},
        {"id": 3, "name": "c"},
    ]
    assert isinstance(table, pa.Table)


def test_execute_query_streamable_skips_global_dataframe_and_legacy_save(tmp_path):
    executor = QueryExecutor(
        engine_factory=MagicMock(),
        save_function=MagicMock(side_effect=AssertionError("legacy save must not run")),
        results_dir=tmp_path,
        logger=MagicMock(),
    )
    conn = MagicMock()
    conn.type = "postgres"

    with (
        patch.object(
            executor,
            "_execute_streaming_to_file",
            return_value=(2, ["id", "name"]),
        ) as mock_stream,
        patch.object(
            executor._df_builder,
            "from_rows",
            side_effect=AssertionError("global DataFrame path must not run"),
        ),
        patch(
            "app.db.query_executor.get_temp_output_path",
            side_effect=lambda name: Path(tmp_path) / name,
        ),
        patch(
            "app.db.query_executor.save_result_blob",
            return_value="res-stream-1",
        ) as mock_save_blob,
    ):
        result = executor.execute_query(
            connection=conn,
            sql="SELECT id, name FROM t",
            project_id="proj-1",
            output_filename="estrai_stream",
            output_format="csv",
            geom_column=None,
            group_id="grp-1",
        )

    mock_stream.assert_called_once()
    mock_save_blob.assert_called_once()
    assert result["success"] is True
    assert result["rows_count"] == 2
    assert result["columns"] == ["id", "name"]
    assert result["result_id"] == "res-stream-1"
    assert result["output_path"].endswith(".csv")
