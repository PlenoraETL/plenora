"""
Rollback regression test per APPEND/UPDATE/UPSERT (C6).

Scopo: garantire che `_execute_all_or_nothing` rollbacki tutte le righe
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


def _oracle_drop(t):
    with oracle_conn() as c:
        cur = c.cursor()
        try: cur.execute(f"DROP TABLE {t} PURGE")
        except Exception: pass
        cur.close()


def _pg_drop(t):
    with postgres_ext_conn() as c:
        with c.cursor() as cur:
            try: cur.execute(f'DROP TABLE IF EXISTS public."{t}"')
            except Exception: pass


def _oracle_fetch_vals(t):
    with oracle_conn() as c:
        cur = c.cursor()
        try:
            cur.execute(f"SELECT ID, VAL FROM {t} ORDER BY ID")
            return [(int(r[0]), r[1]) for r in cur.fetchall()]
        finally:
            cur.close()


def _pg_fetch_vals(t):
    with postgres_ext_conn() as c:
        with c.cursor() as cur:
            cur.execute(f'SELECT id, val FROM public."{t}" ORDER BY id')
            rows = cur.fetchall()
            return [(int(r["id"]), r["val"]) for r in rows]


def _oracle_make_writer(engine, table, mode, match_cols=None):
    # Oracle: colonne dest UPPERCASE per convenzione dialetto
    cols = [
        ColumnMapping(source_column="id", dest_column="ID", dest_type="int",
                      is_primary_key=True),
        ColumnMapping(source_column="val", dest_column="VAL", dest_type="str"),
    ]
    return DatabaseWriter(
        WriteConfig(
            connection_id="c", schema_name=ORACLE_USER.upper(),
            table_name=table, write_mode=mode,
            transaction_mode=TransactionMode.ALL_OR_NOTHING,
            if_table_exists="replace", batch_size=2,
            column_mappings=cols,
            match_columns=match_cols or ["ID"],
            conflict_policy=ConflictPolicy.FORCE,
        ),
        engine=engine, db_type="oracle",
    )


def _pg_make_writer(engine, table, mode, match_cols=None):
    cols = [
        ColumnMapping(source_column="id", dest_column="id", dest_type="int",
                      is_primary_key=True),
        ColumnMapping(source_column="val", dest_column="val", dest_type="str"),
    ]
    return DatabaseWriter(
        WriteConfig(
            connection_id="c", schema_name="public",
            table_name=table, write_mode=mode,
            transaction_mode=TransactionMode.ALL_OR_NOTHING,
            if_table_exists="replace", batch_size=2,
            column_mappings=cols,
            match_columns=match_cols or ["id"],
            conflict_policy=ConflictPolicy.FORCE,
        ),
        engine=engine, db_type="postgres",
    )


def _seed_oracle(table, rows):
    """rows: list[(id, val)]. Usa REPLACE per garantire schema con PK."""
    engine = _oracle_engine()
    w = _oracle_make_writer(engine, table, WriteMode.REPLACE)
    w.write(pd.DataFrame(
        {"id": [r[0] for r in rows], "val": [r[1] for r in rows]}
    ))


def _seed_pg(table, rows):
    engine = _pg_engine()
    w = _pg_make_writer(engine, table, WriteMode.REPLACE)
    w.write(pd.DataFrame(
        {"id": [r[0] for r in rows], "val": [r[1] for r in rows]}
    ))


def _partial_fail_mock():
    """Mock _execute_batch: ritorna il primo batch normalmente, poi
    solleva. Simula fail mid-stream (dopo che qualche batch e' gia'
    andato a buon fine in-memory nella transazione). Se ALL_OR_NOTHING
    funziona, la transazione rollbacka TUTTO e il target resta intatto.
    """
    call_count = [0]

    def mock(original_fn):
        def wrapped(conn, batch_df, columns, sql, operation):
            call_count[0] += 1
            if call_count[0] == 1:
                return original_fn(conn, batch_df, columns, sql, operation)
            raise RuntimeError(f"batch #{call_count[0]} fallito artificialmente")
        return wrapped

    return mock




@pytest.mark.skipif(not _oracle_reachable(), reason="Oracle non raggiungibile")
class TestOracleRollbackRegression:

    def test_append_all_or_nothing_rolls_back_on_mid_stream_failure(self):
        table = make_it_id("ora_rb_app").upper()
        try:
            _seed_oracle(table, [(1, "a"), (2, "b"), (3, "c")])
            pre = _oracle_fetch_vals(table)
            assert pre == [(1, "a"), (2, "b"), (3, "c")]

            engine = _oracle_engine()
            w = _oracle_make_writer(engine, table, WriteMode.APPEND)
            # 5 righe → 3 batch (2+2+1)
            new_df = pd.DataFrame({
                "id": [10, 20, 30, 40, 50],
                "val": ["x", "y", "z", "w", "v"],
            })

            from unittest.mock import patch
            original = w._execute_batch
            wrapper = _partial_fail_mock()(original)
            with patch.object(w, "_execute_batch", side_effect=wrapper):
                try:
                    w.write(new_df)
                except Exception:
                    pass

            post = _oracle_fetch_vals(table)
            assert post == pre, (
                f"APPEND ALL_OR_NOTHING rollback fallito: pre={pre}, "
                f"post={post}. _execute_all_or_nothing deve committare "
                f"tutto o nulla."
            )
        finally:
            _oracle_drop(table)

    def test_update_all_or_nothing_rolls_back_on_mid_stream_failure(self):
        table = make_it_id("ora_rb_upd").upper()
        try:
            _seed_oracle(table, [(1, "a"), (2, "b"), (3, "c"), (4, "d"), (5, "e")])
            pre = _oracle_fetch_vals(table)

            engine = _oracle_engine()
            w = _oracle_make_writer(engine, table, WriteMode.UPDATE)
            # UPDATE 5 righe → 3 batch
            new_df = pd.DataFrame({
                "id": [1, 2, 3, 4, 5],
                "val": ["A", "B", "C", "D", "E"],
            })

            from unittest.mock import patch
            original = w._execute_batch
            wrapper = _partial_fail_mock()(original)
            with patch.object(w, "_execute_batch", side_effect=wrapper):
                try:
                    w.write(new_df)
                except Exception:
                    pass

            post = _oracle_fetch_vals(table)
            assert post == pre, (
                f"UPDATE ALL_OR_NOTHING rollback fallito: pre={pre}, "
                f"post={post}"
            )
        finally:
            _oracle_drop(table)

    def test_upsert_all_or_nothing_rolls_back_on_mid_stream_failure(self):
        table = make_it_id("ora_rb_ups").upper()
        try:
            _seed_oracle(table, [(1, "a"), (2, "b"), (3, "c")])
            pre = _oracle_fetch_vals(table)

            engine = _oracle_engine()
            w = _oracle_make_writer(engine, table, WriteMode.UPSERT)
            # UPSERT 5 righe: 2 update + 3 insert → 3 batch
            new_df = pd.DataFrame({
                "id": [1, 2, 10, 20, 30],
                "val": ["A", "B", "x", "y", "z"],
            })

            from unittest.mock import patch
            original = w._execute_batch
            wrapper = _partial_fail_mock()(original)
            with patch.object(w, "_execute_batch", side_effect=wrapper):
                try:
                    w.write(new_df)
                except Exception:
                    pass

            post = _oracle_fetch_vals(table)
            assert post == pre, (
                f"UPSERT ALL_OR_NOTHING rollback fallito: pre={pre}, "
                f"post={post}"
            )
        finally:
            _oracle_drop(table)




@pytest.mark.skipif(not _pg_reachable(), reason="Postgres non raggiungibile")
class TestPostgresRollbackRegression:

    def test_append_all_or_nothing_rolls_back_on_mid_stream_failure(self):
        table = make_it_id("pg_rb_app")
        try:
            _seed_pg(table, [(1, "a"), (2, "b"), (3, "c")])
            pre = _pg_fetch_vals(table)

            engine = _pg_engine()
            w = _pg_make_writer(engine, table, WriteMode.APPEND)
            new_df = pd.DataFrame({
                "id": [10, 20, 30, 40, 50],
                "val": ["x", "y", "z", "w", "v"],
            })

            from unittest.mock import patch
            original = w._execute_batch
            wrapper = _partial_fail_mock()(original)
            with patch.object(w, "_execute_batch", side_effect=wrapper):
                try:
                    w.write(new_df)
                except Exception:
                    pass

            assert _pg_fetch_vals(table) == pre, "APPEND PG rollback fallito"
        finally:
            _pg_drop(table)

    def test_update_all_or_nothing_rolls_back_on_mid_stream_failure(self):
        table = make_it_id("pg_rb_upd")
        try:
            _seed_pg(table, [(1, "a"), (2, "b"), (3, "c"), (4, "d"), (5, "e")])
            pre = _pg_fetch_vals(table)

            engine = _pg_engine()
            w = _pg_make_writer(engine, table, WriteMode.UPDATE)
            new_df = pd.DataFrame({
                "id": [1, 2, 3, 4, 5],
                "val": ["A", "B", "C", "D", "E"],
            })

            from unittest.mock import patch
            original = w._execute_batch
            wrapper = _partial_fail_mock()(original)
            with patch.object(w, "_execute_batch", side_effect=wrapper):
                try:
                    w.write(new_df)
                except Exception:
                    pass

            assert _pg_fetch_vals(table) == pre, "UPDATE PG rollback fallito"
        finally:
            _pg_drop(table)

    def test_upsert_all_or_nothing_rolls_back_on_mid_stream_failure(self):
        table = make_it_id("pg_rb_ups")
        try:
            _seed_pg(table, [(1, "a"), (2, "b"), (3, "c")])
            pre = _pg_fetch_vals(table)

            engine = _pg_engine()
            w = _pg_make_writer(engine, table, WriteMode.UPSERT)
            new_df = pd.DataFrame({
                "id": [1, 2, 10, 20, 30],
                "val": ["A", "B", "x", "y", "z"],
            })

            from unittest.mock import patch
            original = w._execute_batch
            wrapper = _partial_fail_mock()(original)
            with patch.object(w, "_execute_batch", side_effect=wrapper):
                try:
                    w.write(new_df)
                except Exception:
                    pass

            assert _pg_fetch_vals(table) == pre, "UPSERT PG rollback fallito"
        finally:
            _pg_drop(table)
