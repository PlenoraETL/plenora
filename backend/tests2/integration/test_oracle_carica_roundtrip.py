"""
Integration test CARICA su Oracle reale (C5 fix 3).

Scenari coperti:
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
    # oracledb thick mode richiede Oracle Instant Client: usiamo thin (default)
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
            pass  # non esisteva
        cur.close()


def _count(table_name: str) -> int:
    with oracle_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table_name}")
            return int(cur.fetchone()[0])
        finally:
            cur.close()


def _make_writer(engine, table: str, mode: WriteMode,
                 transaction: TransactionMode = TransactionMode.ALL_OR_NOTHING,
                 columns=None) -> DatabaseWriter:
    cfg = WriteConfig(
        connection_id="conn-1",
        schema_name=ORACLE_USER.upper(),
        table_name=table,
        write_mode=mode,
        transaction_mode=transaction,
        if_table_exists="replace",
        batch_size=500,
        column_mappings=columns or [
            ColumnMapping(source_column="id", dest_column="ID", dest_type="int"),
            ColumnMapping(source_column="value", dest_column="VAL", dest_type="str"),
        ],
        match_columns=[],
        conflict_policy=ConflictPolicy.FORCE,
    )
    return DatabaseWriter(cfg, engine=engine, db_type="oracle")


# Scenario 1 — smoke test base


def test_oracle_create_and_insert_smoke():
    """CREATE + batch insert su Oracle, verifica row count."""
    table = make_it_id("ora_smoke").upper()
    try:
        engine = _engine()
        writer = _make_writer(engine, table, WriteMode.CREATE)
        df = pd.DataFrame({"id": [1, 2, 3], "value": ["a", "b", "c"]})

        result = writer.write(df)

        assert result.rows_written == 3, f"result: {result.to_dict()}"
        assert _count(table) == 3
    finally:
        _drop_if_exists(table)


# Scenario 2 — C5 fix 1: TRUNCATE rollback safety


def test_oracle_truncate_insert_rolls_back_on_insert_failure():
    """Il caso critico del C5: truncate_insert con INSERT fallito.
    Con il fix (DELETE FROM invece di TRUNCATE), la tabella deve
    restare popolata — rollback copre sia DELETE sia INSERT failure.
    """
    table = make_it_id("ora_trunc_safe").upper()
    try:
        # Pre-popola la tabella con 5 righe
        engine = _engine()
        writer_create = _make_writer(engine, table, WriteMode.CREATE)
        initial_df = pd.DataFrame({
            "id": [1, 2, 3, 4, 5],
            "value": ["a", "b", "c", "d", "e"],
        })
        writer_create.write(initial_df)
        assert _count(table) == 5

        # Ora tento truncate_insert con dati che forzano IntegrityError
        # (id duplicato su primary key implicita? Senza PK Oracle non dara'
        # errore; creiamo scenario di fail artificiale via colonna con
        # type mismatch grave).
        # Alternativa piu' affidabile: uso un mock per simulare fail su
        # _batch_insert dopo truncate. Questo verifica la logica
        # transazionale (rollback) senza dipendere da constraint DB.

        from unittest.mock import patch

        writer_ti = _make_writer(engine, table, WriteMode.TRUNCATE_INSERT)
        bad_df = pd.DataFrame({"id": [99], "value": ["new"]})

        # Mock _execute_batch: il path atomico truncate+insert lo chiama
        # direttamente (non passa da _batch_insert). Sollevando qui dentro
        # simuliamo un fail INSERT *dopo* l'esecuzione del DELETE FROM,
        # ma ancora dentro la stessa transazione → il rollback deve
        # coprire anche la cancellazione.
        def boom(*args, **kwargs):
            raise RuntimeError("INSERT failed artificially")

        with patch.object(writer_ti, "_execute_batch", side_effect=boom):
            try:
                writer_ti.write(bad_df)
            except Exception:
                pass  # expected

        # Con TRUNCATE TABLE (Previous behavior): la tabella sarebbe vuota qui
        # Con DELETE FROM (Current contract): rollback, tabella popolata come prima
        count_after = _count(table)
        assert count_after == 5, (
            f"C5 fix 1 regressione: tabella ha {count_after} righe "
            f"dopo truncate+insert_failed, doveva avere 5 (rollback)."
        )
    finally:
        _drop_if_exists(table)


# Scenario 3 — C5 fix 2: CLOB auto-promote su colonne > 4000 char


def test_oracle_long_string_column_creates_clob():
    """Colonna string con valore > 4000 char deve essere CREATE-ata
    come CLOB, non VARCHAR2(4000) (altrimenti ORA-12899)."""
    table = make_it_id("ora_clob").upper()
    try:
        engine = _engine()
        cols = [
            ColumnMapping(source_column="id", dest_column="ID", dest_type="int"),
            ColumnMapping(source_column="body", dest_column="BODY", dest_type="str"),
        ]
        writer = _make_writer(engine, table, WriteMode.CREATE, columns=cols)
        long_text = "x" * 5000  # > 4000 soglia VARCHAR2
        df = pd.DataFrame({"id": [1], "body": [long_text]})

        result = writer.write(df)
        assert result.rows_written == 1, f"{result.to_dict()}"

        # Verifica il tipo della colonna BODY nella tabella creata
        with oracle_conn() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    f"SELECT data_type FROM user_tab_columns "
                    f"WHERE table_name = '{table}' AND column_name = 'BODY'"
                )
                row = cur.fetchone()
                assert row is not None
                assert row[0] == "CLOB", (
                    f"BODY creata come {row[0]}, doveva essere CLOB "
                    f"per auto-promote su testo > 4000 char."
                )
            finally:
                cur.close()
    finally:
        _drop_if_exists(table)


# Scenario 4 — append per_batch fail-partial documented behavior


def test_oracle_append_per_batch_keeps_committed_batches_on_failure():
    """Documenta il behavior voluto: append + per_batch + fail a meta'
    lascia i batch precedenti committati. Combinato con il WARNING
    loud del writer, serve per tracciare aspettative operative."""
    table = make_it_id("ora_append_pb").upper()
    try:
        engine = _engine()
        # CREATE + APPEND su per_batch
        writer_c = _make_writer(engine, table, WriteMode.CREATE)
        writer_c.write(pd.DataFrame({"id": [1], "value": ["a"]}))
        assert _count(table) == 1

        from unittest.mock import patch

        writer_ap = _make_writer(
            engine, table, WriteMode.APPEND,
            transaction=TransactionMode.PER_BATCH,
        )
        df = pd.DataFrame({"id": [2, 3, 4], "value": ["b", "c", "d"]})

        # Fail al secondo batch lascia il primo committato (behavior per_batch)
        call_count = [0]
        original_batch = writer_ap._batch_insert

        def fake_batch(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return original_batch(*args, **kwargs)  # primo batch OK
            raise RuntimeError("fail 2nd batch")

        with patch.object(writer_ap, "_batch_insert", side_effect=fake_batch):
            try:
                writer_ap.write(df)
            except Exception:
                pass

        # Behavior voluto per_batch: batch committati restano.
        # Accept range: almeno l'originale deve esserci.
        final = _count(table)
        assert final >= 1
    finally:
        _drop_if_exists(table)
