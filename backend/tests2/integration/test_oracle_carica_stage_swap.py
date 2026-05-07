"""Integration test stage+swap su Oracle reale (C5 fix 3).

Scenari coperti:
1. Happy path: >= soglia → stage+swap completa + target ha dati nuovi
2. Orfano recovery: shadow `_stg` leftover da run crashed → nuova run
   la droppa e procede correttamente
3. INSERT in shadow fallisce → target INTATTO (dati vecchi preservati,
   shadow cleanup-ata)
4. Threshold NON raggiunto → routing fallback a atomic (non stage+swap)

Requisito: istanza Oracle locale raggiungibile via
tests2/integration/harness/oracle.py. Skip automatico se non disponibile.
"""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import create_engine

from tests2.integration.harness.db import make_it_id
from tests2.integration.harness.oracle import (
    ORACLE_HOST,
    ORACLE_PORT,
    ORACLE_SERVICE,
    ORACLE_USER,
    ORACLE_PASSWORD,
    oracle_conn,
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


def _oracle_reachable() -> bool:
    try:
        with oracle_conn():
            return True
    except Exception:
        return False


pytestmark.append(
    pytest.mark.skipif(not _oracle_reachable(), reason="Oracle non raggiungibile")
)


def _engine():
    url = (
        f"oracle+oracledb://{ORACLE_USER}:{ORACLE_PASSWORD}@"
        f"{ORACLE_HOST}:{ORACLE_PORT}/?service_name={ORACLE_SERVICE}"
    )
    return create_engine(url)


def _drop_if_exists(table_name: str) -> None:
    with oracle_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(f"DROP TABLE {table_name} PURGE")
        except Exception:
            pass
        cur.close()


def _count(table_name: str) -> int:
    with oracle_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table_name}")
            return int(cur.fetchone()[0])
        finally:
            cur.close()


def _table_exists(table_name: str) -> bool:
    with oracle_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                f"SELECT COUNT(*) FROM user_tables WHERE table_name = '{table_name}'"
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
        schema_name=ORACLE_USER.upper(),
        table_name=table,
        write_mode=mode,
        transaction_mode=transaction,
        if_table_exists="replace",
        batch_size=500,
        column_mappings=[
            ColumnMapping(source_column="id", dest_column="ID", dest_type="int"),
            ColumnMapping(source_column="value", dest_column="VAL", dest_type="str"),
        ],
        match_columns=[],
        conflict_policy=ConflictPolicy.FORCE,
    )
    return DatabaseWriter(cfg, engine=engine, db_type="oracle")


# Scenario 1 — happy path stage+swap


def test_stage_swap_happy_path_replaces_target(monkeypatch):
    """>= soglia → stage+swap. Target deve avere i dati nuovi, la
    shadow e il backup non devono restare come orfani."""
    table = make_it_id("ora_swap_ok").upper()
    stg_table = f"{table}__STG"
    bak_table = f"{table}__BAK"
    try:
        # Abbassa soglia a 3 cosi' un df da 4 righe la supera
        monkeypatch.setattr(
            "app.core.carica.database_writer.settings.CARICA_STAGE_SWAP_THRESHOLD",
            3, raising=False,
        )

        engine = _engine()
        # Prepopola target con 5 righe vecchie
        w_create = _make_writer(engine, table, WriteMode.CREATE)
        w_create.write(pd.DataFrame({
            "id": [1, 2, 3, 4, 5],
            "value": ["old_a", "old_b", "old_c", "old_d", "old_e"],
        }))
        assert _count(table) == 5

        # Scatena stage+swap con 4 righe nuove (>= soglia 3)
        w_swap = _make_writer(engine, table, WriteMode.TRUNCATE_INSERT)
        new_df = pd.DataFrame({
            "id": [10, 20, 30, 40],
            "value": ["new_a", "new_b", "new_c", "new_d"],
        })
        result = w_swap.write(new_df)

        assert result.rows_written == 4, f"{result.to_dict()}"
        assert _count(table) == 4
        assert not _table_exists(stg_table), "shadow non droppata"
        assert not _table_exists(bak_table), "backup non droppato"
    finally:
        _drop_if_exists(table)
        _drop_if_exists(stg_table)
        _drop_if_exists(bak_table)


# Scenario 2 — orfano recovery


def test_stage_swap_cleans_orphan_interim_from_prior_crash(monkeypatch):
    """C6: Oracle usa DBMS_REDEFINITION path (non stage+swap). L'orfana
    di riferimento e' `{table}__RDF`, non `{table}__STG`. La Phase 1
    del metodo redef droppa l'interim orfana prima di procedere."""
    table = make_it_id("ora_swap_orf").upper()
    # Tabella con PK per far attivare DBMS_REDEFINITION
    interim_table = f"{table}__RDF"
    try:
        monkeypatch.setattr(
            "app.core.carica.database_writer.settings.CARICA_STAGE_SWAP_THRESHOLD",
            3, raising=False,
        )

        # CREATE con PK (condizione per DBMS_REDEFINITION via CONS_USE_PK)
        with oracle_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"""
                CREATE TABLE {table} (
                    id NUMBER NOT NULL,
                    val VARCHAR2(50),
                    CONSTRAINT PK_{table} PRIMARY KEY (id)
                )
            """)
            cur.execute(f"INSERT INTO {table} VALUES (1, 'x')")
            # Simula interim orfana da run precedente crashata
            cur.execute(f"CREATE TABLE {interim_table} (id NUMBER, val VARCHAR2(50))")
            cur.execute(f"INSERT INTO {interim_table} VALUES (999, 'GARBAGE')")
            cur.close()
        assert _table_exists(interim_table)

        engine = _engine()
        w_swap = _make_writer(engine, table, WriteMode.TRUNCATE_INSERT)
        new_df = pd.DataFrame({
            "id": [100, 200, 300],
            "value": ["a", "b", "c"],
        })
        result = w_swap.write(new_df)

        assert result.rows_written == 3
        assert _count(table) == 3, "Target non aggiornato"
        assert not _table_exists(interim_table), (
            "L'interim orfana __RDF non e' stata droppata dal Phase 1 "
            "del metodo _truncate_insert_oracle_redef"
        )
    finally:
        _drop_if_exists(table)
        _drop_if_exists(interim_table)


# Scenario 3 — INSERT shadow fallisce, target INTATTO


def test_stage_swap_insert_failure_preserves_target_data(monkeypatch):
    """Il killer feature del stage+swap: se l'INSERT nella shadow
    fallisce, il target NON e' mai stato toccato → zero perdita dati."""
    table = make_it_id("ora_swap_fail").upper()
    stg_table = f"{table}__STG"
    bak_table = f"{table}__BAK"
    try:
        monkeypatch.setattr(
            "app.core.carica.database_writer.settings.CARICA_STAGE_SWAP_THRESHOLD",
            3, raising=False,
        )

        engine = _engine()
        # Prepopola target con 7 righe
        w_create = _make_writer(engine, table, WriteMode.CREATE)
        w_create.write(pd.DataFrame({
            "id": list(range(1, 8)),
            "value": [f"v{i}" for i in range(1, 8)],
        }))
        assert _count(table) == 7

        # Mock _execute_batch per far fallire l'INSERT nella shadow
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

        # Target DEVE essere ancora a 7 righe vecchie
        assert _count(table) == 7, (
            f"Target manomesso: {_count(table)} righe invece di 7. "
            f"Stage+swap deve proteggere il target in caso di INSERT fail."
        )
        # Shadow cleanup-ata
        assert not _table_exists(stg_table)
        # Backup non deve essere stato creato (swap non e' avvenuto)
        assert not _table_exists(bak_table)
    finally:
        _drop_if_exists(table)
        _drop_if_exists(stg_table)
        _drop_if_exists(bak_table)


# Scenario 4 — threshold NOT raggiunto: routing atomic (no swap)


def test_below_threshold_uses_atomic_not_stage_swap(monkeypatch):
    """Con soglia alta e df piccolo, il writer NON deve usare stage+swap
    (niente shadow/backup creati)."""
    table = make_it_id("ora_atomic_rt").upper()
    stg_table = f"{table}__STG"
    bak_table = f"{table}__BAK"
    try:
        monkeypatch.setattr(
            "app.core.carica.database_writer.settings.CARICA_STAGE_SWAP_THRESHOLD",
            10_000,  # alta
            raising=False,
        )

        engine = _engine()
        w_create = _make_writer(engine, table, WriteMode.CREATE)
        w_create.write(pd.DataFrame({"id": [1, 2], "value": ["a", "b"]}))
        assert _count(table) == 2

        # df sotto soglia → path atomic
        w = _make_writer(engine, table, WriteMode.TRUNCATE_INSERT)
        new_df = pd.DataFrame({"id": [10, 20, 30], "value": ["x", "y", "z"]})
        result = w.write(new_df)

        assert result.rows_written == 3
        assert _count(table) == 3
        # Stage+swap NON deve aver creato artifatti
        assert not _table_exists(stg_table)
        assert not _table_exists(bak_table)
    finally:
        _drop_if_exists(table)
        _drop_if_exists(stg_table)
        _drop_if_exists(bak_table)
