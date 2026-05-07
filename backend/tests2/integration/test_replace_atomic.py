"""
Integration test REPLACE atomic (C6 critical fix).

Pre-fix: REPLACE faceva drop_table() + create_table() + _batch_insert()
"""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import create_engine

from tests2.integration.harness.db import make_it_id
from tests2.integration.harness.oracle import (
    ORACLE_HOST, ORACLE_PORT, ORACLE_SERVICE,
    ORACLE_USER, ORACLE_PASSWORD, oracle_conn,
)
from tests2.integration.harness.postgres_ext import (
    postgres_ext_conn, postgres_ext_sqlalchemy_url,
)

from app.core.carica.database_writer import DatabaseWriter
from app.core.carica.models import (
    ColumnMapping, ConflictPolicy, TransactionMode,
    WriteMode, WriteConfig,
)


pytestmark = [pytest.mark.integration, pytest.mark.carica, pytest.mark.contract]


def _oracle_reachable() -> bool:
    try:
        with oracle_conn():
            return True
    except Exception:
        return False


def _pg_reachable() -> bool:
    try:
        with postgres_ext_conn():
            return True
    except Exception:
        return False


def _oracle_engine():
    return create_engine(
        f"oracle+oracledb://{ORACLE_USER}:{ORACLE_PASSWORD}@"
        f"{ORACLE_HOST}:{ORACLE_PORT}/?service_name={ORACLE_SERVICE}"
    )


def _pg_engine():
    return create_engine(postgres_ext_sqlalchemy_url())


def _oracle_drop(table: str) -> None:
    with oracle_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(f"DROP TABLE {table} PURGE")
        except Exception:
            pass
        cur.close()


def _pg_drop(table: str) -> None:
    with postgres_ext_conn() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(f'DROP TABLE IF EXISTS public."{table}"')
            except Exception:
                pass


def _oracle_count(table: str) -> int:
    with oracle_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            return int(cur.fetchone()[0])
        finally:
            cur.close()


def _pg_count(table: str) -> int:
    with postgres_ext_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) AS c FROM public."{table}"')
            return int(cur.fetchone()["c"])


def _oracle_exists(table: str) -> bool:
    with oracle_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                f"SELECT COUNT(*) FROM user_tables WHERE table_name = '{table}'"
            )
            return int(cur.fetchone()[0]) > 0
        finally:
            cur.close()


def _pg_exists(table: str) -> bool:
    with postgres_ext_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS c FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = %s",
                (table,),
            )
            return int(cur.fetchone()["c"]) > 0


def _oracle_writer(engine, table: str) -> DatabaseWriter:
    cfg = WriteConfig(
        connection_id="c", schema_name=ORACLE_USER.upper(), table_name=table,
        write_mode=WriteMode.REPLACE,
        transaction_mode=TransactionMode.ALL_OR_NOTHING,
        if_table_exists="replace", batch_size=500,
        column_mappings=[
            ColumnMapping(source_column="id", dest_column="ID", dest_type="int"),
            ColumnMapping(source_column="val", dest_column="VAL", dest_type="str"),
        ],
        match_columns=[], conflict_policy=ConflictPolicy.FORCE,
    )
    return DatabaseWriter(cfg, engine=engine, db_type="oracle")


def _pg_writer(engine, table: str) -> DatabaseWriter:
    cfg = WriteConfig(
        connection_id="c", schema_name="public", table_name=table,
        write_mode=WriteMode.REPLACE,
        transaction_mode=TransactionMode.ALL_OR_NOTHING,
        if_table_exists="replace", batch_size=500,
        column_mappings=[
            ColumnMapping(source_column="id", dest_column="id", dest_type="int"),
            ColumnMapping(source_column="val", dest_column="val", dest_type="str"),
        ],
        match_columns=[], conflict_policy=ConflictPolicy.FORCE,
    )
    return DatabaseWriter(cfg, engine=engine, db_type="postgres")


# Oracle scenarios


@pytest.mark.skipif(not _oracle_reachable(), reason="Oracle non raggiungibile")
class TestOracleReplaceAtomic:

    def test_happy_path_replaces_target(self):
        table = make_it_id("ora_repl_ok").upper()
        try:
            engine = _oracle_engine()
            # Popolo il target con dati vecchi tramite REPLACE iniziale
            w1 = _oracle_writer(engine, table)
            w1.write(pd.DataFrame({"id": [1, 2], "val": ["old_a", "old_b"]}))
            assert _oracle_count(table) == 2

            # REPLACE con dati nuovi
            w2 = _oracle_writer(engine, table)
            result = w2.write(pd.DataFrame({
                "id": [10, 20, 30], "val": ["new_a", "new_b", "new_c"],
            }))
            assert result.rows_written == 3
            assert _oracle_count(table) == 3
        finally:
            _oracle_drop(table)
            _oracle_drop(f"{table}__STG")
            _oracle_drop(f"{table}__BAK")

    def test_insert_failure_preserves_target_data(self):
        """Il killer feature del fix: se l'INSERT fallisce durante REPLACE,
        i dati vecchi del target restano. Pre-fix: tabella scomparsa."""
        table = make_it_id("ora_repl_fail").upper()
        try:
            engine = _oracle_engine()
            w1 = _oracle_writer(engine, table)
            w1.write(pd.DataFrame({"id": [1, 2, 3], "val": ["v1", "v2", "v3"]}))
            assert _oracle_count(table) == 3

            from unittest.mock import patch

            w2 = _oracle_writer(engine, table)

            def boom(*args, **kwargs):
                raise RuntimeError("INSERT fallito artificialmente")

            with patch.object(w2, "_execute_batch", side_effect=boom):
                try:
                    w2.write(pd.DataFrame({
                        "id": [10, 20], "val": ["new_a", "new_b"],
                    }))
                except Exception:
                    pass

            # Il test chiave: target con dati vecchi preservato
            assert _oracle_exists(table), (
                f"REGRESSIONE C6: target {table} scomparso dopo "
                f"REPLACE con INSERT fallito (data loss deterministica)"
            )
            assert _oracle_count(table) == 3, (
                f"Target manomesso: {_oracle_count(table)} righe invece di 3"
            )
        finally:
            _oracle_drop(table)
            _oracle_drop(f"{table}__STG")
            _oracle_drop(f"{table}__BAK")

    def test_replace_on_nonexistent_table_creates_it(self):
        """REPLACE su target che non esiste → opera come CREATE."""
        table = make_it_id("ora_repl_new").upper()
        try:
            assert not _oracle_exists(table)
            engine = _oracle_engine()
            w = _oracle_writer(engine, table)
            result = w.write(pd.DataFrame({
                "id": [1, 2, 3], "val": ["a", "b", "c"],
            }))
            assert result.rows_written == 3
            assert _oracle_exists(table)
            assert _oracle_count(table) == 3
        finally:
            _oracle_drop(table)
            _oracle_drop(f"{table}__STG")
            _oracle_drop(f"{table}__BAK")


# Postgres scenarios


@pytest.mark.skipif(not _pg_reachable(), reason="Postgres non raggiungibile")
class TestPostgresReplaceAtomic:

    def test_happy_path_replaces_target(self):
        table = make_it_id("pg_repl_ok")
        try:
            engine = _pg_engine()
            w1 = _pg_writer(engine, table)
            w1.write(pd.DataFrame({"id": [1, 2], "val": ["old_a", "old_b"]}))
            assert _pg_count(table) == 2

            w2 = _pg_writer(engine, table)
            result = w2.write(pd.DataFrame({
                "id": [10, 20, 30], "val": ["new_a", "new_b", "new_c"],
            }))
            assert result.rows_written == 3
            assert _pg_count(table) == 3
        finally:
            _pg_drop(table)
            _pg_drop(f"{table}__stg")
            _pg_drop(f"{table}__bak")

    def test_insert_failure_preserves_target_data(self):
        table = make_it_id("pg_repl_fail")
        try:
            engine = _pg_engine()
            w1 = _pg_writer(engine, table)
            w1.write(pd.DataFrame({"id": [1, 2, 3], "val": ["v1", "v2", "v3"]}))
            assert _pg_count(table) == 3

            from unittest.mock import patch

            w2 = _pg_writer(engine, table)

            def boom(*args, **kwargs):
                raise RuntimeError("INSERT fallito artificialmente")

            with patch.object(w2, "_execute_batch", side_effect=boom):
                try:
                    w2.write(pd.DataFrame({
                        "id": [10, 20], "val": ["new_a", "new_b"],
                    }))
                except Exception:
                    pass

            assert _pg_exists(table), (
                f"REGRESSIONE C6 Postgres: target {table} scomparso dopo "
                f"REPLACE con INSERT fallito"
            )
            assert _pg_count(table) == 3
        finally:
            _pg_drop(table)
            _pg_drop(f"{table}__stg")
            _pg_drop(f"{table}__bak")

    def test_replace_on_nonexistent_table_creates_it(self):
        table = make_it_id("pg_repl_new")
        try:
            assert not _pg_exists(table)
            engine = _pg_engine()
            w = _pg_writer(engine, table)
            result = w.write(pd.DataFrame({
                "id": [1, 2, 3], "val": ["a", "b", "c"],
            }))
            assert result.rows_written == 3
            assert _pg_exists(table)
            assert _pg_count(table) == 3
        finally:
            _pg_drop(table)
            _pg_drop(f"{table}__stg")
            _pg_drop(f"{table}__bak")
