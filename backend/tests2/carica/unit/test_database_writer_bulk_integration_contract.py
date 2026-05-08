"""Contract tests for the Carica batch execution path.

The old fake bulk dispatcher was removed: `_execute_batch` must use the
real executemany path for inserts, updates and upserts.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from sqlalchemy.sql.elements import TextClause

from app.core.carica.models import ColumnMapping


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _col(src: str, **kw) -> ColumnMapping:
    return ColumnMapping(source_column=src, dest_column=src, **kw)


def _make_writer(db_type: str = "postgres"):
    from app.core.carica.database_writer import DatabaseWriter

    writer = DatabaseWriter.__new__(DatabaseWriter)
    writer.db_type = db_type
    config = MagicMock()
    config.schema_name = "public"
    config.table_name = "users"
    writer.config = config
    return writer


@pytest.fixture
def df():
    return pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})


@pytest.fixture
def cols():
    return [_col("id"), _col("name")]


@pytest.fixture
def conn():
    return MagicMock()


def test_insert_uses_executemany_directly(df, cols, conn):
    writer = _make_writer("postgres")

    with patch.object(
        writer, "_build_params", return_value={"id": 1, "name": "a"}
    ) as build_params:
        n = writer._execute_batch(conn, df, cols, "INSERT INTO users ...", "insert")

    assert n == 3
    assert build_params.call_count == 3
    conn.execute.assert_called_once()
    sql_arg, params_arg = conn.execute.call_args.args
    assert isinstance(sql_arg, TextClause)
    assert params_arg == [
        {"id": 1, "name": "a"},
        {"id": 1, "name": "a"},
        {"id": 1, "name": "a"},
    ]


@pytest.mark.parametrize("op", ["update", "upsert"])
def test_non_insert_operations_use_same_executemany_path(op, df, cols, conn):
    writer = _make_writer("postgres")

    with patch.object(writer, "_build_params", return_value={"id": 1}):
        n = writer._execute_batch(conn, df, cols, "UPDATE users ...", op)

    assert n == 3
    conn.execute.assert_called_once()


def test_geometry_insert_uses_executemany_without_bulk_fallback(df, conn):
    writer = _make_writer("postgres")
    geo_cols = [_col("id"), _col("geom", is_geometry=True)]
    df_geo = pd.DataFrame({"id": [1], "geom": ["POINT(0 0)"]})

    with patch.object(
        writer, "_build_params", return_value={"id": 1, "geom": "POINT(0 0)"}
    ):
        n = writer._execute_batch(conn, df_geo, geo_cols, "INSERT...", "insert")

    assert n == 1
    conn.execute.assert_called_once()


def test_empty_batch_returns_zero_without_executing(cols, conn):
    writer = _make_writer("postgres")
    empty_df = pd.DataFrame({"id": [], "name": []})

    n = writer._execute_batch(conn, empty_df, cols, "INSERT...", "insert")

    assert n == 0
    conn.execute.assert_not_called()


def test_target_table_and_schema_are_accepted_for_stage_swap_callers(df, cols, conn):
    writer = _make_writer("postgres")

    with patch.object(writer, "_build_params", return_value={"id": 1}):
        n = writer._execute_batch(
            conn,
            df,
            cols,
            'INSERT INTO "public"."users__stg" ...',
            "insert",
            target_table="users__stg",
            target_schema="public",
        )

    assert n == 3
    conn.execute.assert_called_once()


def test_stage_swap_callsite_still_passes_target_table_to_execute_batch():
    """Keep stage/swap callsites explicit while the signature remains compat."""
    import ast
    import inspect

    from app.core.carica.database_writer import DatabaseWriter

    methods = {
        "_replace_atomic": DatabaseWriter._replace_atomic,
        "_truncate_insert_stage_swap": DatabaseWriter._truncate_insert_stage_swap,
        "_truncate_insert_oracle_redef": DatabaseWriter._truncate_insert_oracle_redef,
    }

    missing: list[str] = []
    for method_name, method in methods.items():
        try:
            method_src = inspect.getsource(method)
        except (OSError, TypeError) as e:
            pytest.fail(f"inspect.getsource({method.__qualname__}) failed: {e}")

        tree = ast.parse("class _S:\n" + method_src)
        has_target = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr != "_execute_batch":
                continue
            has_target = has_target or any(
                keyword.arg == "target_table" for keyword in node.keywords
            )

        if not has_target:
            missing.append(method_name)

    assert missing == []


@pytest.fixture
def writer_caplog(caplog):
    target = logging.getLogger("app.core.carica.writer")
    target.addHandler(caplog.handler)
    orig_level = target.level
    target.setLevel(logging.DEBUG)
    try:
        yield caplog
    finally:
        target.removeHandler(caplog.handler)
        target.setLevel(orig_level)


def _stage_swap_records(caplog):
    return [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "stage_swap.dispatched"
    ]


def test_stage_swap_dispatched_emits_for_mysql(writer_caplog):
    from app.core.carica.database_writer import DatabaseWriter

    writer = DatabaseWriter.__new__(DatabaseWriter)
    writer.db_type = "mysql"
    config = MagicMock()
    config.schema_name = "mydb"
    config.table_name = "users"
    writer.config = config
    ddl = MagicMock()
    ddl.generate_drop_if_exists.side_effect = RuntimeError("halt after log")
    writer.ddl = ddl

    df_ = pd.DataFrame({"id": [1, 2, 3]})
    cols_ = [_col("id")]
    result = MagicMock()

    with pytest.raises(RuntimeError, match="halt after log"):
        writer._truncate_insert_stage_swap(df_, cols_, result)

    records = _stage_swap_records(writer_caplog)
    assert len(records) == 1
    record = records[0]
    assert record.levelname == "INFO"
    assert record.dialect == "mysql"
    assert record.path == "stage_swap"
    assert record.schema == "mydb"
    assert record.table == "users"
    assert record.rows == 3


def test_oracle_redef_dispatched_emits_for_oracle(writer_caplog):
    from app.core.carica.database_writer import DatabaseWriter

    writer = DatabaseWriter.__new__(DatabaseWriter)
    writer.db_type = "oracle"
    config = MagicMock()
    config.schema_name = "hr"
    config.table_name = "employees"
    writer.config = config
    ddl = MagicMock()
    ddl.generate_insert.side_effect = RuntimeError("halt after log")
    writer.ddl = ddl

    df_ = pd.DataFrame({"id": [1, 2]})
    cols_ = [_col("id")]
    result = MagicMock()

    with pytest.raises(RuntimeError, match="halt after log"):
        writer._truncate_insert_oracle_redef(df_, cols_, result)

    records = _stage_swap_records(writer_caplog)
    assert len(records) == 1
    record = records[0]
    assert record.levelname == "INFO"
    assert record.dialect == "oracle"
    assert record.path == "oracle_redef"
    assert record.schema == "HR"
    assert record.table == "EMPLOYEES"
    assert record.rows == 2
