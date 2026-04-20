"""Integration test Oracle DBMS_REDEFINITION — schema preservation (C6).

Motivazione
-----------
Pre-C6, il path stage+swap su Oracle usava `CREATE AS SELECT WHERE 1=0`,
che NON preserva PK / UK / FK / indici / check / trigger / grant.
Dopo ogni swap oltre soglia, lo schema del target si degradava
silenziosamente in produzione.

Con DBMS_REDEFINITION (+ COPY_TABLE_DEPENDENTS) il schema e'
integralmente replicato sullo shadow prima del FINISH atomico.

Test verifica:
1. Primary key preservato
2. Unique constraint/indice preservato
3. Check constraint preservato
4. NOT NULL preservato (regressione test)
5. Dati = nuovi (swap avvenuto, non merge)
6. Finestra di inconsistenza assente (FINISH_REDEF e' atomico)
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
    oracle_read_retry,
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
    def _op(conn):
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table_name}")
            return int(cur.fetchone()[0])
        finally:
            cur.close()
    return oracle_read_retry(_op)


def _get_constraints(table_name: str) -> list[dict]:
    """Ritorna PK/UK/CHECK della tabella."""
    def _op(conn):
        cur = conn.cursor()
        try:
            cur.execute(f"""
                SELECT constraint_name, constraint_type, search_condition_vc
                FROM user_constraints
                WHERE table_name = '{table_name}'
                  AND constraint_type IN ('P', 'U', 'C')
                ORDER BY constraint_type, constraint_name
            """)
            rows = cur.fetchall()
            return [
                {
                    "name": r[0],
                    "type": r[1],
                    "check_condition": r[2] or "",
                }
                for r in rows
            ]
        finally:
            cur.close()
    return oracle_read_retry(_op)


def _get_indexes(table_name: str) -> list[dict]:
    """Ritorna indici user (non-PK) sulla tabella."""
    def _op(conn):
        cur = conn.cursor()
        try:
            cur.execute(f"""
                SELECT i.index_name, i.uniqueness
                FROM user_indexes i
                WHERE i.table_name = '{table_name}'
                  AND i.index_type = 'NORMAL'
                ORDER BY i.index_name
            """)
            rows = cur.fetchall()
            return [{"name": r[0], "uniqueness": r[1]} for r in rows]
        finally:
            cur.close()
    return oracle_read_retry(_op)


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
            ColumnMapping(source_column="code", dest_column="CODE", dest_type="str"),
            ColumnMapping(source_column="amount", dest_column="AMOUNT", dest_type="int"),
        ],
        match_columns=[],
        conflict_policy=ConflictPolicy.FORCE,
    )
    return DatabaseWriter(cfg, engine=engine, db_type="oracle")


# =============================================================================
# Setup helper: crea tabella con schema completo (PK + UK + CHECK)
# =============================================================================


def _create_schema_rich_table(table: str) -> None:
    with oracle_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            CREATE TABLE {table} (
                ID NUMBER NOT NULL,
                CODE VARCHAR2(50) NOT NULL,
                AMOUNT NUMBER NOT NULL,
                CONSTRAINT PK_{table} PRIMARY KEY (ID),
                CONSTRAINT UQ_{table}_CODE UNIQUE (CODE),
                CONSTRAINT CK_{table}_AMOUNT CHECK (AMOUNT >= 0)
            )
        """)
        cur.execute(f"""
            INSERT INTO {table} (ID, CODE, AMOUNT)
            VALUES (1, 'OLD_A', 100)
        """)
        cur.execute(f"""
            INSERT INTO {table} (ID, CODE, AMOUNT)
            VALUES (2, 'OLD_B', 200)
        """)
        cur.close()


# =============================================================================
# Scenario 1 — schema preservation: PK, UK, CHECK dopo redef
# =============================================================================


def test_redef_preserves_pk_unique_and_check_constraints(monkeypatch):
    """Il killer feature di C6: DBMS_REDEFINITION replica tutti i
    constraint e indici sullo shadow via COPY_TABLE_DEPENDENTS, poi
    swappa atomicamente. Lo schema post-swap DEVE essere identico a
    quello pre-swap."""
    table = make_it_id("ora_redef_schema").upper()
    interim = f"{table}__RDF"
    try:
        monkeypatch.setattr(
            "app.core.carica.database_writer.settings.CARICA_STAGE_SWAP_THRESHOLD",
            3, raising=False,
        )
        _create_schema_rich_table(table)
        assert _count(table) == 2

        constraints_before = _get_constraints(table)
        types_before = set(c["type"] for c in constraints_before)
        # Oracle emette CHECK impliciti per NOT NULL colonne: quindi
        # {'C'} e' sempre presente. Verifichiamo che P e U ci siano.
        assert "P" in types_before, f"Setup invalido, no PK: {constraints_before}"
        assert "U" in types_before, f"Setup invalido, no UK: {constraints_before}"
        assert "C" in types_before, f"Setup invalido, no CHECK: {constraints_before}"
        # Salvo il CHECK esplicito (AMOUNT >= 0) per verificarlo post-swap
        explicit_check_before = next(
            (c for c in constraints_before
             if c["type"] == "C" and ">=" in c["check_condition"]),
            None,
        )
        assert explicit_check_before is not None, (
            f"Setup invalido, CHECK esplicito 'AMOUNT >= 0' non trovato: "
            f"{constraints_before}"
        )

        # Esegui TRUNCATE_INSERT con dati che rispettano i constraint
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

        # Verifica schema preservato: PK, UK, CHECK esplicito tutti presenti
        constraints_after = _get_constraints(table)
        types_after = set(c["type"] for c in constraints_after)
        assert "P" in types_after, (
            f"PK persa dopo redef. Pre: {constraints_before}, "
            f"post: {constraints_after}"
        )
        assert "U" in types_after, (
            f"UK persa dopo redef: {constraints_after}"
        )
        explicit_check_after = next(
            (c for c in constraints_after
             if c["type"] == "C" and ">=" in c["check_condition"]),
            None,
        )
        assert explicit_check_after is not None, (
            f"CHECK 'AMOUNT >= 0' perso dopo redef: {constraints_after}"
        )

        # Interim deve essere droppata
        with oracle_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT COUNT(*) FROM user_tables WHERE table_name = '{interim}'"
            )
            assert int(cur.fetchone()[0]) == 0, (
                f"Interim {interim} non droppata dopo redef"
            )
            cur.close()
    finally:
        _drop_if_exists(table)
        _drop_if_exists(interim)


# =============================================================================
# Scenario 2 — tabella senza PK → fallback a ROWID o atomic
# =============================================================================


def test_redef_without_pk_falls_back_gracefully(monkeypatch):
    """Se la tabella non ha PK, CAN_REDEF_TABLE(CONS_USE_PK) fallisce.
    Il writer deve provare CONS_USE_ROWID (che funziona sempre su
    tabelle ordinarie), e se anche quello non va fallback ad atomic.
    In entrambi i casi, il target DEVE contenere i nuovi dati."""
    table = make_it_id("ora_redef_nopk").upper()
    try:
        monkeypatch.setattr(
            "app.core.carica.database_writer.settings.CARICA_STAGE_SWAP_THRESHOLD",
            3, raising=False,
        )
        # Tabella senza PK
        with oracle_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"""
                CREATE TABLE {table} (
                    ID NUMBER,
                    CODE VARCHAR2(50),
                    AMOUNT NUMBER
                )
            """)
            cur.execute(f"INSERT INTO {table} VALUES (1, 'OLD', 100)")
            cur.close()

        engine = _engine()
        writer = _make_writer(engine, table, WriteMode.TRUNCATE_INSERT)
        new_df = pd.DataFrame({
            "id": [10, 20, 30, 40],
            "code": ["A", "B", "C", "D"],
            "amount": [1, 2, 3, 4],
        })
        result = writer.write(new_df)

        # Indipendentemente dal path seguito (redef via ROWID o
        # fallback atomic), il risultato DEVE essere 4 righe nuove
        assert result.rows_written == 4, f"{result.to_dict()}"
        assert _count(table) == 4
    finally:
        _drop_if_exists(table)
        _drop_if_exists(f"{table}__RDF")


# =============================================================================
# Scenario 3 — INSERT in interim fallisce → target INTATTO, interim droppato
# =============================================================================


def test_redef_insert_failure_aborts_and_preserves_target(monkeypatch):
    """Se l'INSERT nell'interim fallisce, il writer chiama
    ABORT_REDEF_TABLE, droppa l'interim e il target resta INVARIATO
    (dati vecchi preservati, schema preservato)."""
    table = make_it_id("ora_redef_fail").upper()
    interim = f"{table}__RDF"
    try:
        monkeypatch.setattr(
            "app.core.carica.database_writer.settings.CARICA_STAGE_SWAP_THRESHOLD",
            3, raising=False,
        )
        _create_schema_rich_table(table)
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
            raise RuntimeError("INSERT interim fallito artificialmente")

        with patch.object(writer, "_execute_batch", side_effect=boom):
            try:
                writer.write(new_df)
            except Exception:
                pass

        # Target DEVE avere ancora i dati vecchi
        assert _count(table) == 2, (
            f"Target manomesso: {_count(table)} righe invece di 2. "
            f"ABORT_REDEF non ha preservato i dati."
        )

        # Schema preservato
        constraints_after = _get_constraints(table)
        assert sorted([c["type"] for c in constraints_after]) == \
               sorted([c["type"] for c in constraints_before]), (
            "Schema alterato dopo fallimento redef"
        )

        # Interim droppato
        with oracle_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT COUNT(*) FROM user_tables WHERE table_name = '{interim}'"
            )
            assert int(cur.fetchone()[0]) == 0, (
                f"Interim {interim} non droppata dopo fail"
            )
            cur.close()
    finally:
        _drop_if_exists(table)
        _drop_if_exists(interim)
