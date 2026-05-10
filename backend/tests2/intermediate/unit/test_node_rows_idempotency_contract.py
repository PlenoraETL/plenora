"""
Idempotency + atomicity contract per node_rows / node_meta.

Scope:
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.intermediate,
    pytest.mark.contract,
    pytest.mark.regression,
]


# Helper: cattura tutte le chiamate cur.execute / cur.executemany dentro
# una `with transaction() as conn: with conn.cursor() as cur:` finta.


def _make_fake_transaction():
    """Ritorna `(fake_tx, cur, conn)` dove fake_tx e' un context manager
    compatibile con db.transaction() e cur e' il MagicMock del cursor
    su cui si possono ispezionare execute/executemany.

    Il cursor stesso e' anche un context manager per supportare
    `with conn.cursor() as cur:` come usato dal codice.
    """
    cur = MagicMock(name="cursor")

    cursor_cm = MagicMock(name="cursor_cm")
    cursor_cm.__enter__ = MagicMock(return_value=cur)
    cursor_cm.__exit__ = MagicMock(return_value=False)

    conn = MagicMock(name="conn")
    conn.cursor.return_value = cursor_cm

    @contextmanager
    def fake_tx():
        yield conn

    return fake_tx, cur, conn


@contextmanager
def _patch_db_io(idb):
    """Patcha sia `idb.transaction` sia `idb.execute_values` (Bug #22).

    Rende osservabili in un singolo gancio:
      - le chiamate al cursor (execute, ecc.) per DELETE / UPSERT meta
      - le chiamate batch INSERT che ora passano per
        `psycopg2.extras.execute_values` invece di `cur.executemany`.

    Yields `(cur, ev_spy)` cosi' i test possono ispezionare entrambi.
    """
    fake_tx, cur, _conn = _make_fake_transaction()
    ev_spy = MagicMock(name="execute_values_spy")
    with patch.object(idb, "transaction", fake_tx), \
         patch.object(idb, "execute_values", ev_spy):
        yield cur, ev_spy


def _delete_calls(cur) -> list:
    """Filtra le chiamate cur.execute(...) che cominciano per DELETE FROM node_rows."""
    return [
        c for c in cur.execute.call_args_list
        if c.args and "DELETE FROM node_rows" in c.args[0]
    ]


def _insert_row_calls(cur, ev_spy=None) -> list:
    """Filtra le chiamate batch INSERT su `node_rows`.

    Bug #22 (perf, 2026-04-28): Current contract l'INSERT batch passa per
    `psycopg2.extras.execute_values` (1 round-trip) invece di
    `cur.executemany` (500 round-trip serializzati). Restano comunque
    riconoscibili come "INSERT INTO node_rows ... VALUES %s" su
    `execute_values(cur, sql, records, ...)`. I test passano
    `ev_spy` per inspect:
        ev_spy.call_args_list -> [call(cur, sql, records, ...), ...]
    Adattiamo allo shape `(sql_str, records_list)` di executemany
    per non duplicare la logica di assert nei test esistenti.
    """
    if ev_spy is not None:
        results = []
        for c in ev_spy.call_args_list:
            sql_arg = None
            records_arg = None
            if len(c.args) >= 3:
                sql_arg = c.args[1]
                records_arg = c.args[2]
            elif "sql" in c.kwargs and "argslist" in c.kwargs:
                sql_arg = c.kwargs["sql"]
                records_arg = c.kwargs["argslist"]
            if sql_arg and "INSERT INTO node_rows" in sql_arg:
                # Mimica lo shape vecchio (sql, records) -> Munge call
                fake = MagicMock()
                fake.args = (sql_arg, records_arg)
                results.append(fake)
        return results
    return [
        c for c in cur.executemany.call_args_list
        if c.args and "INSERT INTO node_rows" in c.args[0]
    ]


def _upsert_meta_calls(cur) -> list:
    """Filtra le chiamate cur.execute(INSERT INTO node_meta ... ON CONFLICT ...)."""
    return [
        c for c in cur.execute.call_args_list
        if c.args
        and "INSERT INTO node_meta" in c.args[0]
        and "ON CONFLICT" in c.args[0]
    ]


# 1. insert_node_rows e' idempotente: DELETE precede INSERT


def test_insert_node_rows_emits_delete_before_insert():
    """Singola invocazione: DELETE FROM node_rows ... eseguita PRIMA della
    INSERT batch. Ordine garantito dentro la stessa tx.

    Bug #22 (perf): l'INSERT batch passa per `execute_values`, ispezione
    via `ev_spy`. La sequenza temporale resta DELETE -> INSERT -> ...
    """
    from app.core.intermediate import db as idb

    rows = [{"_row_hash": "h1", "a": 1}, {"_row_hash": "h2", "a": 2}]
    schema = {"a": "int64"}

    with _patch_db_io(idb) as (cur, ev_spy):
        idb.insert_node_rows("run-1", "node-x", rows, schema)

    deletes = _delete_calls(cur)
    inserts = _insert_row_calls(cur, ev_spy=ev_spy)
    assert len(deletes) == 1, f"atteso 1 DELETE, trovati: {cur.execute.call_args_list}"
    assert len(inserts) == 1, f"atteso 1 INSERT batch, trovati: {ev_spy.call_args_list}"
    # DELETE filtra per (run_id, node_id)
    assert deletes[0].args[1] == ("run-1", "node-x")
    # INSERT batch contiene 2 record con row_idx 0 e 1
    insert_records = inserts[0].args[1]
    assert len(insert_records) == 2
    assert [r[2] for r in insert_records] == [0, 1]  # row_idx
    assert [r[3] for r in insert_records] == ["h1", "h2"]  # row_hash


def test_insert_node_rows_double_invocation_evicts_previous():
    """Doppia invocazione per stesso (run_id, node_id): ogni chiamata
    emette il proprio DELETE prima del proprio INSERT. Il DB vede:
    DELETE → INSERT(N1) → DELETE → INSERT(N2). Nessuna duplicazione."""
    from app.core.intermediate import db as idb

    rows1 = [{"_row_hash": "h1", "a": 1}]
    rows2 = [{"_row_hash": "h2", "a": 2}, {"_row_hash": "h3", "a": 3}]
    schema = {"a": "int64"}

    with _patch_db_io(idb) as (cur, ev_spy):
        idb.insert_node_rows("run-1", "node-x", rows1, schema)
        idb.insert_node_rows("run-1", "node-x", rows2, schema)

    deletes = _delete_calls(cur)
    inserts = _insert_row_calls(cur, ev_spy=ev_spy)
    # Due cicli completi
    assert len(deletes) == 2
    assert len(inserts) == 2
    # Entrambi i DELETE sono per la stessa coppia
    assert deletes[0].args[1] == ("run-1", "node-x")
    assert deletes[1].args[1] == ("run-1", "node-x")
    # Le ultime righe inserite (rows2) sono quelle effettive
    last_records = inserts[1].args[1]
    assert len(last_records) == 2
    assert [r[3] for r in last_records] == ["h2", "h3"]


def test_insert_node_rows_empty_rows_still_emits_delete():
    """Anche con rows=[] eseguiamo il DELETE: previene l'inconsistenza
    "node_meta.row_count=0 ma node_rows residue da run Previous behavior"."""
    from app.core.intermediate import db as idb

    with _patch_db_io(idb) as (cur, ev_spy):
        idb.insert_node_rows("run-empty", "node-x", [], {"a": "int64"})

    deletes = _delete_calls(cur)
    inserts = _insert_row_calls(cur, ev_spy=ev_spy)
    assert len(deletes) == 1
    assert deletes[0].args[1] == ("run-empty", "node-x")
    # Nessun INSERT batch (rows vuoto)
    assert len(inserts) == 0
    ev_spy.assert_not_called()


# 2. write_node_atomic: idempotency + atomicity end-to-end


def test_write_node_atomic_emits_delete_insert_upsert_in_order():
    """Sequenza dentro singola transazione:
        DELETE FROM node_rows
        execute_values INSERT INTO node_rows VALUES %s
        INSERT INTO node_meta ... ON CONFLICT DO UPDATE
    """
    from app.core.intermediate import db as idb

    rows = [{"_row_hash": "h1", "a": 1}, {"_row_hash": "h2", "a": 2}]
    schema = {"a": "int64"}

    with _patch_db_io(idb) as (cur, ev_spy):
        idb.write_node_atomic(
            "run-A", "node-1",
            node_type="estrai", rows=rows, col_schema=schema,
            exec_time=0.5, status="ok",
        )

    # 1 DELETE + 1 batch INSERT + 1 INSERT meta
    assert len(_delete_calls(cur)) == 1
    assert len(_insert_row_calls(cur, ev_spy=ev_spy)) == 1
    assert len(_upsert_meta_calls(cur)) == 1

    # Ordine: il DELETE precede l'INSERT meta (verifica via
    # call ordering su cur.execute: l'ultimo e' l'upsert meta).
    all_executes = cur.execute.call_args_list
    assert "DELETE FROM node_rows" in all_executes[0].args[0]
    assert "INSERT INTO node_meta" in all_executes[-1].args[0]
    assert "ON CONFLICT" in all_executes[-1].args[0]


def test_write_node_atomic_meta_row_count_matches_inserted_rows():
    """node_meta.row_count = len(rows) effettivamente inseriti.
    Coerenza meta-rows garantita per costruzione (stessa tx)."""
    from app.core.intermediate import db as idb

    rows = [{"_row_hash": "h1", "a": 1}, {"_row_hash": "h2", "a": 2},
            {"_row_hash": "h3", "a": 3}]

    with _patch_db_io(idb) as (cur, _ev_spy):
        idb.write_node_atomic(
            "run-rc", "node-rc",
            node_type="manipola", rows=rows, col_schema={"a": "int64"},
            exec_time=1.0, status="ok",
        )

    upserts = _upsert_meta_calls(cur)
    assert len(upserts) == 1
    meta_params = upserts[0].args[1]
    # Tupla: (run_id, node_id, node_type, row_count, col_schema, exec_time, status, error_msg)
    assert meta_params[0] == "run-rc"
    assert meta_params[1] == "node-rc"
    assert meta_params[2] == "manipola"
    assert meta_params[3] == 3  # row_count = len(rows inseriti)
    assert meta_params[6] == "ok"


def test_write_node_atomic_double_invocation_no_duplicates_meta_consistent():
    """Due chiamate consecutive (es. retry Celery) per stesso
    (run_id, node_id): la seconda DELETE rimuove la prima INSERT,
    la seconda INSERT-meta upserta. Stato finale = solo seconda scrittura."""
    from app.core.intermediate import db as idb

    rows1 = [{"_row_hash": "h1", "a": 1}]
    rows2 = [{"_row_hash": "h2", "a": 2}, {"_row_hash": "h3", "a": 3}]
    schema = {"a": "int64"}

    with _patch_db_io(idb) as (cur, ev_spy):
        idb.write_node_atomic(
            "run-2x", "node-y", node_type="estrai",
            rows=rows1, col_schema=schema, exec_time=0.1, status="ok",
        )
        idb.write_node_atomic(
            "run-2x", "node-y", node_type="estrai",
            rows=rows2, col_schema=schema, exec_time=0.2, status="ok",
        )

    # Due cicli completi
    assert len(_delete_calls(cur)) == 2
    assert len(_insert_row_calls(cur, ev_spy=ev_spy)) == 2
    assert len(_upsert_meta_calls(cur)) == 2

    # L'ultimo upsert meta riflette rows2 (row_count=2)
    last_meta = _upsert_meta_calls(cur)[1]
    assert last_meta.args[1][3] == 2  # row_count finale


def test_write_node_atomic_empty_rows_still_evicts_and_upserts_meta():
    """rows=[] con status='ok' (caso edge): DELETE comunque + nessun
    INSERT batch + upsert meta con row_count=0. Garantisce consistency
    quando un retry "vuoto" deve sovrascrivere una run precedente che
    aveva inserito righe."""
    from app.core.intermediate import db as idb

    with _patch_db_io(idb) as (cur, ev_spy):
        idb.write_node_atomic(
            "run-e", "node-e", node_type="estrai",
            rows=[], col_schema={"a": "int64"},
            exec_time=0.0, status="ok",
        )

    assert len(_delete_calls(cur)) == 1
    assert len(_insert_row_calls(cur, ev_spy=ev_spy)) == 0  # nessun INSERT batch
    ev_spy.assert_not_called()
    upserts = _upsert_meta_calls(cur)
    assert len(upserts) == 1
    assert upserts[0].args[1][3] == 0  # row_count=0


# 3. Atomicity: rollback su fallimento meta evita orfani in node_rows


def test_write_node_atomic_meta_fail_rollback_propagates():
    """Se l'INSERT su node_meta fallisce, l'eccezione propaga fuori dal
    `with transaction()` → pg_connection.transaction() esegue rollback
    automatico (cattura+rollback+re-raise nel context manager). Il
    contract qui pinna SOLO la propagazione: il rollback del DELETE+INSERT
    in node_rows e' garantito strutturalmente da PG, non da codice
    application-side, e si verifica integration-side.
    """
    from app.core.intermediate import db as idb

    fake_tx, cur, _conn = _make_fake_transaction()
    ev_spy = MagicMock(name="execute_values_spy")

    # Simula fallimento solo sull'INSERT meta. Per gli altri execute
    # ritorna None (no-op) — non vogliamo richiamare il mock stesso
    # (causerebbe RecursionError perche' side_effect intercetta tutti i
    # call sul mock, incluso il nostro fallback).
    def execute_side_effect(sql, *_args, **_kwargs):
        if "INSERT INTO node_meta" in sql:
            raise RuntimeError("meta INSERT fail simulated")
        return None

    cur.execute.side_effect = execute_side_effect

    with patch.object(idb, "transaction", fake_tx), \
         patch.object(idb, "execute_values", ev_spy):
        with pytest.raises(RuntimeError, match="meta INSERT fail simulated"):
            idb.write_node_atomic(
                "run-rb", "node-rb", node_type="estrai",
                rows=[{"_row_hash": "h1", "a": 1}],
                col_schema={"a": "int64"},
                exec_time=0.0, status="ok",
            )

    # DELETE chiamato (prima del fail)
    assert any(
        "DELETE FROM node_rows" in str(c)
        for c in cur.execute.call_args_list
    )
    # INSERT batch chiamato (prima del fail meta)
    assert len(_insert_row_calls(cur, ev_spy=ev_spy)) == 1


# 4. writer.write_node usa write_node_atomic sul ramo success


def test_writer_write_node_success_branch_routes_through_atomic_helper():
    """node-write atomicity contract: writer.write_node (status='ok' + rows non vuoti)
    chiama `db.write_node_atomic` UNA volta e NON chiama piu'
    `db.insert_node_rows` o `db.upsert_node_meta` separatamente.
    Garantisce che il refactor sia stato applicato end-to-end."""
    from app.core.intermediate import writer as w

    rows = [{"col_a": 1}, {"col_a": 2}]
    schema = {"col_a": "int64"}

    with patch.object(w.db, "write_node_atomic") as mock_atomic, \
         patch.object(w.db, "insert_node_rows") as mock_insert, \
         patch.object(w.db, "upsert_node_meta") as mock_meta:
        result = w.write_node(
            run_id="run-r", node_id="node-r", node_type="estrai",
            rows=rows, col_schema=schema, exec_time=0.4, status="ok",
        )

    mock_atomic.assert_called_once()
    # I due helper "vecchi" non vengono piu' chiamati direttamente sul
    # ramo success (sono inglobati in write_node_atomic, singola tx).
    mock_insert.assert_not_called()
    mock_meta.assert_not_called()
    # L'hash aggregato e' restituito normalmente
    assert isinstance(result, str)
    assert len(result) == 64


def test_writer_write_node_error_branch_keeps_meta_only_path():
    """node-write atomicity contract: ramo error/empty (status != 'ok' o rows vuoto) NON
    passa per write_node_atomic — solo upsert_node_meta. Preserva
    comportamento storico dei caller (manipola/arricchisci/carica) che
    chiamano write_node con status='error' o rows=[]."""
    from app.core.intermediate import writer as w

    with patch.object(w.db, "write_node_atomic") as mock_atomic, \
         patch.object(w.db, "upsert_node_meta") as mock_meta:
        # Caso 1: status='ok' ma rows=[]
        w.write_node(
            run_id="run-empty", node_id="node-empty", node_type="estrai",
            rows=[], col_schema={"a": "int64"},
            exec_time=0.1, status="ok",
        )
        # Caso 2: status='error' (rows ignorati)
        w.write_node(
            run_id="run-err", node_id="node-err", node_type="estrai",
            rows=None, col_schema={},
            exec_time=0.0, status="error", error_msg="boom",
        )

    mock_atomic.assert_not_called()
    # 2 chiamate dirette a upsert_node_meta (una per ogni caso)
    assert mock_meta.call_count == 2
