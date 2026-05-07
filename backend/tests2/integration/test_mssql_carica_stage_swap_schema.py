"""
Integration test MSSQL stage+swap — schema preservation (C6).

Motivazione
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


def _get_constraints(table_name: str) -> list[dict]:
    with sqlserver_conn() as conn:
        cur = conn.cursor()
        try:
            # PK + UK via key_constraints
            cur.execute(f"""
                SELECT kc.name, kc.type_desc
                FROM sys.key_constraints kc
                JOIN sys.objects o ON kc.parent_object_id = o.object_id
                WHERE o.schema_id = SCHEMA_ID('dbo')
                  AND o.name = '{table_name}'
            """)
            results = [
                {"name": r[0], "type": r[1]}
                for r in cur.fetchall()
            ]
            # CHECK via check_constraints
            cur.execute(f"""
                SELECT cc.name, cc.definition
                FROM sys.check_constraints cc
                JOIN sys.objects o ON cc.parent_object_id = o.object_id
                WHERE o.schema_id = SCHEMA_ID('dbo')
                  AND o.name = '{table_name}'
            """)
            for r in cur.fetchall():
                results.append(
                    {"name": r[0], "type": "CHECK", "definition": r[1]}
                )
            return results
        finally:
            cur.close()


def _get_indexes(table_name: str) -> list[dict]:
    with sqlserver_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(f"""
                SELECT i.name, i.is_unique, i.is_primary_key, i.is_unique_constraint
                FROM sys.indexes i
                JOIN sys.objects o ON i.object_id = o.object_id
                WHERE o.schema_id = SCHEMA_ID('dbo')
                  AND o.name = '{table_name}'
                  AND i.type > 0
                  AND i.is_primary_key = 0
                  AND i.is_unique_constraint = 0
            """)
            return [
                {"name": r[0], "unique": bool(r[1])}
                for r in cur.fetchall()
            ]
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
            ColumnMapping(source_column="code", dest_column="code", dest_type="str"),
            ColumnMapping(source_column="amount", dest_column="amount", dest_type="int"),
        ],
        match_columns=[],
        conflict_policy=ConflictPolicy.FORCE,
    )
    return DatabaseWriter(cfg, engine=engine, db_type="mssql")


def _create_schema_rich_mssql_table(table: str) -> None:
    with sqlserver_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            CREATE TABLE dbo.[{table}] (
                id INT NOT NULL,
                code NVARCHAR(50) NOT NULL,
                amount INT NOT NULL,
                CONSTRAINT PK_{table} PRIMARY KEY (id),
                CONSTRAINT UQ_{table}_code UNIQUE (code),
                CONSTRAINT CK_{table}_amt CHECK (amount >= 0)
            )
        """)
        cur.execute(
            f"CREATE INDEX IX_{table}_amt ON dbo.[{table}] (amount)"
        )
        cur.execute(
            f"INSERT INTO dbo.[{table}] (id, code, amount) VALUES (1, 'OLD_A', 100)"
        )
        cur.execute(
            f"INSERT INTO dbo.[{table}] (id, code, amount) VALUES (2, 'OLD_B', 200)"
        )
        cur.close()


# Scenario 1 — schema preservation post-swap


def test_mssql_stage_swap_preserves_pk_uk_check_and_index(monkeypatch):
    """PK, UK, CHECK, indice non-unique tutti preservati post stage+swap
    (introspection + replay + post-swap rename restaura nomi originali)."""
    table = make_it_id("ms_swap_schema")
    try:
        monkeypatch.setattr(
            "app.core.carica.database_writer.settings.CARICA_STAGE_SWAP_THRESHOLD",
            3, raising=False,
        )

        _create_schema_rich_mssql_table(table)
        assert _count(table) == 2

        constraints_before = _get_constraints(table)
        indexes_before = _get_indexes(table)
        types_before = sorted([c["type"] for c in constraints_before])
        # PK, UK, CHECK presenti
        assert any(c["type"] == "PRIMARY_KEY_CONSTRAINT" for c in constraints_before)
        assert any(c["type"] == "UNIQUE_CONSTRAINT" for c in constraints_before)
        assert any(c["type"] == "CHECK" for c in constraints_before)
        assert len(indexes_before) == 1
        assert not indexes_before[0]["unique"]

        engine = _engine()
        writer = _make_writer(engine, table, WriteMode.TRUNCATE_INSERT)
        new_df = pd.DataFrame({
            "id": [10, 20, 30, 40],
            "code": ["NEW_A", "NEW_B", "NEW_C", "NEW_D"],
            "amount": [111, 222, 333, 444],
        })
        result = writer.write(new_df)

        assert result.rows_written == 4, f"{result.to_dict()}"
        assert _count(table) == 4

        # Schema preservato
        constraints_after = _get_constraints(table)
        indexes_after = _get_indexes(table)

        assert any(c["type"] == "PRIMARY_KEY_CONSTRAINT" for c in constraints_after), (
            f"PK perso dopo swap: {constraints_after}"
        )
        assert any(c["type"] == "UNIQUE_CONSTRAINT" for c in constraints_after), (
            f"UK perso dopo swap: {constraints_after}"
        )
        assert any(c["type"] == "CHECK" for c in constraints_after), (
            f"CHECK perso dopo swap: {constraints_after}"
        )
        assert len(indexes_after) == 1, (
            f"Indice non-unique perso dopo swap: {indexes_after}"
        )

        # Nomi originali restaurati (senza suffisso __stg)
        for c in constraints_after:
            assert not c["name"].endswith("__stg"), (
                f"Constraint {c['name']} ha ancora suffisso __stg "
                f"(PHASE 6 sp_rename non eseguita)"
            )
        for i in indexes_after:
            assert not i["name"].endswith("__stg"), (
                f"Index {i['name']} ha ancora suffisso __stg"
            )
    finally:
        _drop_if_exists(table)
        _drop_if_exists(f"{table}__stg")
        _drop_if_exists(f"{table}__bak")


# Scenario 2 — INSERT fail: target INTATTO + schema INTATTO


def test_mssql_stage_swap_insert_failure_preserves_target_and_schema(monkeypatch):
    """Se l'INSERT nella shadow fallisce, il target non e' mai stato
    toccato (dati vecchi + schema originali intatti)."""
    table = make_it_id("ms_swap_fail_sch")
    try:
        monkeypatch.setattr(
            "app.core.carica.database_writer.settings.CARICA_STAGE_SWAP_THRESHOLD",
            3, raising=False,
        )
        _create_schema_rich_mssql_table(table)
        assert _count(table) == 2

        constraints_before = _get_constraints(table)

        from unittest.mock import patch

        engine = _engine()
        writer = _make_writer(engine, table, WriteMode.TRUNCATE_INSERT)
        new_df = pd.DataFrame({
            "id": [10, 20, 30, 40],
            "code": ["A", "B", "C", "D"],
            "amount": [1, 2, 3, 4],
        })

        def boom(*args, **kwargs):
            raise RuntimeError("INSERT shadow fallito artificialmente")

        with patch.object(writer, "_execute_batch", side_effect=boom):
            try:
                writer.write(new_df)
            except Exception:
                pass

        assert _count(table) == 2, (
            f"Target manomesso su MSSQL: {_count(table)} righe invece di 2"
        )

        constraints_after = _get_constraints(table)
        names_before = sorted(c["name"] for c in constraints_before)
        names_after = sorted(c["name"] for c in constraints_after)
        assert names_after == names_before, (
            f"Schema alterato dopo fail. Pre: {names_before}, "
            f"post: {names_after}"
        )
    finally:
        _drop_if_exists(table)
        _drop_if_exists(f"{table}__stg")
        _drop_if_exists(f"{table}__bak")
