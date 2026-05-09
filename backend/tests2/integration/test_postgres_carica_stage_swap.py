"""
Integration test stage+swap su Postgres reale (C5 fix 3).

Stessi 4 scenari di test_oracle_carica_stage_swap.py — Postgres e'
"""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import create_engine

from tests2.integration.harness.db import make_it_id
from tests2.integration.harness.postgres_ext import (
    postgres_ext_conn,
    postgres_ext_sqlalchemy_url,
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


def _pg_reachable() -> bool:
    try:
        with postgres_ext_conn():
            return True
    except Exception:
        return False


pytestmark.append(
    pytest.mark.skipif(not _pg_reachable(), reason="Postgres non raggiungibile")
)


def _engine():
    return create_engine(postgres_ext_sqlalchemy_url())


def _drop_if_exists(table_name: str) -> None:
    with postgres_ext_conn() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(f'DROP TABLE IF EXISTS public."{table_name}"')
            except Exception:
                pass


def _count(table_name: str) -> int:
    with postgres_ext_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) AS c FROM public."{table_name}"')
            return int(cur.fetchone()["c"])


def _table_exists(table_name: str) -> bool:
    with postgres_ext_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS c FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = %s",
                (table_name,),
            )
            return int(cur.fetchone()["c"]) > 0


def _make_writer(
    engine, table: str, mode: WriteMode,
    transaction: TransactionMode = TransactionMode.ALL_OR_NOTHING,
) -> DatabaseWriter:
    cfg = WriteConfig(
        connection_id="conn-1",
        schema_name="public",
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
    return DatabaseWriter(cfg, engine=engine, db_type="postgres")


# Scenario 1 — happy path


def test_stage_swap_happy_path_replaces_target(monkeypatch):
    table = make_it_id("pg_swap_ok")
    stg_table = f"{table}__stg"
    bak_table = f"{table}__bak"
    try:
        monkeypatch.setenv("CARICA_STAGE_SWAP_THRESHOLD", "3")

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


# Scenario 2 — orfano recovery


def test_stage_swap_cleans_orphan_shadow_from_prior_crash(monkeypatch):
    table = make_it_id("pg_swap_orf")
    stg_table = f"{table}__stg"
    bak_table = f"{table}__bak"
    try:
        monkeypatch.setenv("CARICA_STAGE_SWAP_THRESHOLD", "3")

        engine = _engine()
        w_create = _make_writer(engine, table, WriteMode.CREATE)
        w_create.write(pd.DataFrame({"id": [1], "value": ["x"]}))

        with postgres_ext_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f'CREATE TABLE public."{stg_table}" '
                    f'(id INTEGER, val VARCHAR(50))'
                )
                cur.execute(
                    f'INSERT INTO public."{stg_table}" VALUES (999, %s)',
                    ("GARBAGE",),
                )
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


# Scenario 3 — INSERT shadow fallisce → target INTATTO


def test_stage_swap_insert_failure_preserves_target_data(monkeypatch):
    table = make_it_id("pg_swap_fail")
    stg_table = f"{table}__stg"
    bak_table = f"{table}__bak"
    try:
        monkeypatch.setenv("CARICA_STAGE_SWAP_THRESHOLD", "3")

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
            f"Target manomesso su Postgres: {_count(table)} righe invece di 7."
        )
        assert not _table_exists(stg_table)
        assert not _table_exists(bak_table)
    finally:
        _drop_if_exists(table)
        _drop_if_exists(stg_table)
        _drop_if_exists(bak_table)


# Scenario 4 — sotto soglia: atomic path


def test_below_threshold_uses_atomic_not_stage_swap(monkeypatch):
    table = make_it_id("pg_atomic_rt")
    stg_table = f"{table}__stg"
    bak_table = f"{table}__bak"
    try:
        monkeypatch.setenv("CARICA_STAGE_SWAP_THRESHOLD", "10000")

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
