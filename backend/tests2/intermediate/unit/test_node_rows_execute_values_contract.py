"""
Contract test Bug #22 — `node_rows` batch insert via `execute_values`.

Pre-fix: `cur.executemany(_SQL_INSERT_NODE_ROW, records)` mandava 500
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


def _make_fake_transaction():
    cur = MagicMock(name="cursor")
    cursor_cm = MagicMock(name="cursor_cm")
    cursor_cm.__enter__ = MagicMock(return_value=cur)
    cursor_cm.__exit__ = MagicMock(return_value=False)
    conn = MagicMock(name="conn")
    conn.cursor.return_value = cursor_cm

    @contextmanager
    def fake_tx():
        yield conn

    return fake_tx, cur


@contextmanager
def _patched(idb):
    fake_tx, cur = _make_fake_transaction()
    ev_spy = MagicMock(name="execute_values_spy")
    with patch.object(idb, "transaction", fake_tx), \
         patch.object(idb, "execute_values", ev_spy):
        yield cur, ev_spy


# A. write_node_snapshot_atomic (path Batch 1, preview_only)


def test_snapshot_atomic_uses_execute_values_with_jsonb_template():
    """Il path checkpoint/preview deve passare per execute_values, non
    per cur.executemany. Verifica anche il template `::jsonb` cast."""
    from app.core.intermediate import db as idb

    rows = [{"_row_hash": f"h{i}", "a": i} for i in range(500)]

    with _patched(idb) as (cur, ev_spy):
        idb.write_node_snapshot_atomic(
            run_id="r1",
            node_id="n1",
            node_type="transform",
            rows_to_persist=rows,
            col_schema={"a": "int64"},
            exec_time=0.0,
            status="ok",
            row_count=500,
            persistence_mode="preview_only",
            preview_truncated=False,
            artifact_status="none",
        )

    # Una sola chiamata a execute_values con 500 record.
    assert ev_spy.call_count == 1, (
        f"Atteso 1 execute_values, trovate {ev_spy.call_count}: "
        f"{ev_spy.call_args_list}"
    )
    call = ev_spy.call_args
    # call.args = (cursor, sql_str, records, *positional)
    assert call.args[0] is cur, "Primo arg = cursor"
    sql = call.args[1]
    records = call.args[2]
    assert "INSERT INTO node_rows" in sql
    assert "VALUES %s" in sql
    assert len(records) == 500
    # Template jsonb cast presente nei kwargs.
    assert call.kwargs.get("template") == "(%s, %s, %s, %s, %s::jsonb)"

    # cur.executemany NON deve essere chiamato per node_rows.
    for c in cur.executemany.call_args_list:
        if c.args and "INSERT INTO node_rows" in c.args[0]:
            raise AssertionError(
                f"cur.executemany NON deve scrivere node_rows post-#22: {c}"
            )


def test_snapshot_atomic_no_execute_values_when_no_rows():
    """metadata_only / dataset vuoto: niente INSERT batch, solo
    DELETE + UPSERT meta. execute_values non deve essere chiamato."""
    from app.core.intermediate import db as idb

    with _patched(idb) as (cur, ev_spy):
        idb.write_node_snapshot_atomic(
            run_id="r1",
            node_id="n1",
            node_type="transform",
            rows_to_persist=[],
            col_schema={"a": "int64"},
            row_count=2000,
            persistence_mode="metadata_only",
        )

    ev_spy.assert_not_called()
    # DELETE comunque (idempotency)
    assert any(
        "DELETE FROM node_rows" in str(c) for c in cur.execute.call_args_list
    )


# B. write_node_atomic (legacy)


def test_write_node_atomic_legacy_uses_execute_values():
    from app.core.intermediate import db as idb

    rows = [{"_row_hash": "h1", "a": 1}, {"_row_hash": "h2", "a": 2}]

    with _patched(idb) as (cur, ev_spy):
        idb.write_node_atomic(
            "rA", "nA",
            node_type="estrai",
            rows=rows,
            col_schema={"a": "int64"},
            exec_time=0.1,
            status="ok",
        )

    assert ev_spy.call_count == 1
    assert ev_spy.call_args.kwargs.get("template") == "(%s, %s, %s, %s, %s::jsonb)"
    assert len(ev_spy.call_args.args[2]) == 2


# C. insert_node_rows (legacy idempotent helper)


def test_insert_node_rows_uses_execute_values():
    from app.core.intermediate import db as idb

    rows = [{"_row_hash": "h1", "a": 1}, {"_row_hash": "h2", "a": 2},
            {"_row_hash": "h3", "a": 3}]

    with _patched(idb) as (cur, ev_spy):
        idb.insert_node_rows("rB", "nB", rows, {"a": "int64"})

    assert ev_spy.call_count == 1
    assert ev_spy.call_args.kwargs.get("template") == "(%s, %s, %s, %s, %s::jsonb)"
    assert len(ev_spy.call_args.args[2]) == 3


# D. Records preservano shape pre-fix (jsonb-encodable)


def test_records_have_5_tuple_with_jsonb_string_payload():
    """Anti-regression: ogni record passato a execute_values e' una
    tupla 5-ari (run_id, node_id, row_idx, row_hash, jsonb_string).
    `_build_node_row_records` non e' cambiato; il test pinna che
    nessun refactor lo abbia rotto silenziosamente."""
    from app.core.intermediate import db as idb

    rows = [
        {"_row_hash": "h1", "a": 1, "name": "x"},
        {"_row_hash": "h2", "a": 2, "name": "y"},
    ]

    with _patched(idb) as (_cur, ev_spy):
        idb.insert_node_rows("rid", "nid", rows, {"a": "int64", "name": "object"})

    records = ev_spy.call_args.args[2]
    assert len(records) == 2
    for idx, rec in enumerate(records):
        assert len(rec) == 5
        run_id, node_id, row_idx, row_hash, jsonb_str = rec
        assert run_id == "rid"
        assert node_id == "nid"
        assert row_idx == idx
        assert row_hash == f"h{idx + 1}"
        assert isinstance(jsonb_str, str)
        # Deve essere JSON parsabile e contenere i dati senza _row_hash.
        import json as _json
        parsed = _json.loads(jsonb_str)
        assert "_row_hash" not in parsed
        assert "a" in parsed
        assert "name" in parsed


# E. Page size cap (sicurezza su payload molto grandi, full_pg)


def test_execute_values_uses_explicit_page_size():
    """`execute_values(page_size=...)` controlla il batch interno: psycopg2
    splitta in piu' INSERT se la lista e' grande. Il valore esplicito
    rende deterministico il comportamento e prevent il default 100
    (che imporrebbe 5 round-trip su 500 righe)."""
    from app.core.intermediate import db as idb

    rows = [{"_row_hash": f"h{i}", "a": i} for i in range(50)]

    with _patched(idb) as (_cur, ev_spy):
        idb.insert_node_rows("r", "n", rows, {"a": "int64"})

    # page_size deve essere esplicito e >= 500 (default preview)
    page_size = ev_spy.call_args.kwargs.get("page_size")
    assert page_size is not None, "page_size non deve essere lasciato al default"
    assert page_size >= 500, f"page_size={page_size} troppo basso per preview da 500"
