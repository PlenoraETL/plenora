"""
Integration test stage+swap su SQL Server reale (C5 fix 3).

Stessi 4 scenari di test_oracle_carica_stage_swap.py.
"""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import create_engine

from tests2.integration.harness.db import make_it_id
from tests2.integration.harness.sqlserver import (
    sqlalchemy_conn_url_candidates,
    sqlserver_conn,
)

from app.core.carica.database_writer import DatabaseWriter
from app.core.carica.models import (
    ColumnMapping,
    ConflictPolicy,
    TransactionMode,
    WriteMode,
    WriteConfig,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.carica,
    pytest.mark.contract,
]


def _mssql_reachable() -> bool:
    try:
        with sqlserver_conn():
            return True
    except Exception:
        return False


pytestmark.append(
    pytest.mark.skipif(not _mssql_reachable(), reason="MSSQL non raggiungibile")
)


def _engine():
    last_error = None
    for url in sqlalchemy_conn_url_candidates():
        try:
            engine = create_engine(url)
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            return engine
        except Exception as e:
            last_error = e
    pytest.skip(f"MSSQL SQLAlchemy engine non disponibile: {last_error}")


def _drop_if_exists(table_name: str) -> None:
    with sqlserver_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                f"IF OBJECT_ID('dbo.[{table_name}]', 'U') IS NOT NULL "
                f"DROP TABLE dbo.[{table_name}]"
            )
        except Exception:
            pass
        cur.close()


def _count(table_name: str) -> int:
    with sqlserver_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT COUNT(*) FROM dbo.[{table_name}]")
            return int(cur.fetchone()[0])
        finally:
            cur.close()


def _table_exists(table_name: str) -> bool:
    with sqlserver_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                f"SELECT COUNT(*) FROM information_schema.tables "
                f"WHERE table_schema = 'dbo' AND table_name = ?",
                (table_name,),
            )
            return int(cur.fetchone()[0]) > 0
        finally:
            cur.close()


def _make_writer(
    engine, table: str, mode: WriteMode,
    transaction: TransactionMode = TransactionMode.ALL_OR_NOTHING,
) -> DatabaseWriter:
    cfg = WriteConfig(
        connection_id="conn-1",
        schema_name="dbo",
        table_name=table,
        write_mode=mode,
        transaction_mode=transaction,
        if_table_exists="replace",
        batch_size=500,
        column_mappings=[
            ColumnMapping(source_column="id", dest_column="id", dest_type="int"),
            ColumnMapping(source_column="value", dest_column="val", dest_type="str"),
        ],
        match_columns=[],
        conflict_policy=ConflictPolicy.FORCE,
    )
    return DatabaseWriter(cfg, engine=engine, db_type="mssql")


def test_stage_swap_happy_path_replaces_target(monkeypatch):
    table = make_it_id("ms_swap_ok")
    stg_table = f"{table}__stg"
    bak_table = f"{table}__bak"
    try:
        monkeypatch.setattr(
            "app.core.carica.database_writer.settings.CARICA_STAGE_SWAP_THRESHOLD",
            3, raising=False,
        )
        engine = _engine()
        w_create = _make_writer(engine, table, WriteMode.CREATE)
        w_create.write(pd.DataFrame({
            "id": [1, 2, 3, 4, 5],
            "value": ["old_a", "old_b", "old_c", "old_d", "old_e"],
        }))
        assert _count(table) == 5

        w_swap = _make_writer(engine, table, WriteMode.TRUNCATE_INSERT)
        new_df = pd.DataFrame({
            "id": [10, 20, 30, 40],
            "value": ["new_a", "new_b", "new_c", "new_d"],
        })
        result = w_swap.write(new_df)

        assert result.rows_written == 4, f"{result.to_dict()}"
        assert _count(table) == 4
        assert not _table_exists(stg_table)
        assert not _table_exists(bak_table)
    finally:
        _drop_if_exists(table)
        _drop_if_exists(stg_table)
        _drop_if_exists(bak_table)


def test_stage_swap_cleans_orphan_shadow_from_prior_crash(monkeypatch):
    table = make_it_id("ms_swap_orf")
    stg_table = f"{table}__stg"
    bak_table = f"{table}__bak"
    try:
        monkeypatch.setattr(
            "app.core.carica.database_writer.settings.CARICA_STAGE_SWAP_THRESHOLD",
            3, raising=False,
        )
        engine = _engine()
        w_create = _make_writer(engine, table, WriteMode.CREATE)
        w_create.write(pd.DataFrame({"id": [1], "value": ["x"]}))

        with sqlserver_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"CREATE TABLE dbo.[{stg_table}] (id INT, val NVARCHAR(50))"
            )
            cur.execute(
                f"INSERT INTO dbo.[{stg_table}] VALUES (999, 'GARBAGE')"
            )
            cur.close()
        assert _table_exists(stg_table)

        w_swap = _make_writer(engine, table, WriteMode.TRUNCATE_INSERT)
        new_df = pd.DataFrame({
            "id": [100, 200, 300],
            "value": ["a", "b", "c"],
        })
        result = w_swap.write(new_df)

        assert result.rows_written == 3
        assert _count(table) == 3
        assert not _table_exists(stg_table)
    finally:
        _drop_if_exists(table)
        _drop_if_exists(stg_table)
        _drop_if_exists(bak_table)


def test_stage_swap_insert_failure_preserves_target_data(monkeypatch):
    table = make_it_id("ms_swap_fail")
    stg_table = f"{table}__stg"
    bak_table = f"{table}__bak"
    try:
        monkeypatch.setattr(
            "app.core.carica.database_writer.settings.CARICA_STAGE_SWAP_THRESHOLD",
            3, raising=False,
        )
        engine = _engine()
        w_create = _make_writer(engine, table, WriteMode.CREATE)
        w_create.write(pd.DataFrame({
            "id": list(range(1, 8)),
            "value": [f"v{i}" for i in range(1, 8)],
        }))
        assert _count(table) == 7

        from unittest.mock import patch

        w_swap = _make_writer(engine, table, WriteMode.TRUNCATE_INSERT)
        new_df = pd.DataFrame({
            "id": [100, 200, 300, 400],
            "value": ["a", "b", "c", "d"],
        })

        def boom(*args, **kwargs):
            raise RuntimeError("INSERT shadow fallito artificialmente")

        with patch.object(w_swap, "_execute_batch", side_effect=boom):
            try:
                w_swap.write(new_df)
            except Exception:
                pass

        assert _count(table) == 7, (
            f"Target manomesso su MSSQL: {_count(table)} righe invece di 7."
        )
        assert not _table_exists(stg_table)
        assert not _table_exists(bak_table)
    finally:
        _drop_if_exists(table)
        _drop_if_exists(stg_table)
        _drop_if_exists(bak_table)


def test_below_threshold_uses_atomic_not_stage_swap(monkeypatch):
    table = make_it_id("ms_atomic_rt")
    stg_table = f"{table}__stg"
    bak_table = f"{table}__bak"
    try:
        monkeypatch.setattr(
            "app.core.carica.database_writer.settings.CARICA_STAGE_SWAP_THRESHOLD",
            10_000, raising=False,
        )
        engine = _engine()
        w_create = _make_writer(engine, table, WriteMode.CREATE)
        w_create.write(pd.DataFrame({"id": [1, 2], "value": ["a", "b"]}))
        assert _count(table) == 2

        w = _make_writer(engine, table, WriteMode.TRUNCATE_INSERT)
        new_df = pd.DataFrame({"id": [10, 20, 30], "value": ["x", "y", "z"]})
        result = w.write(new_df)

        assert result.rows_written == 3
        assert _count(table) == 3
        assert not _table_exists(stg_table)
        assert not _table_exists(bak_table)
    finally:
        _drop_if_exists(table)
        _drop_if_exists(stg_table)
        _drop_if_exists(bak_table)
