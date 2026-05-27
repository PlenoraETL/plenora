"""
Contract test Bug #22 — `node_rows` batch insert via psycopg3 executemany.

Previous behavior: `cur.executemany(_SQL_INSERT_NODE_ROW, records)` mandava 500
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
    with patch.object(idb, "unit_of_work", fake_tx):
        yield cur, cur.executemany


# A. write_node_snapshot_atomic (path current contract, preview_only)


def test_snapshot_atomic_uses_executemany_with_jsonb_cast():
    """Il path checkpoint/preview deve usare executemany.
    Verifica anche il template `::jsonb` cast."""
    from app.core.intermediate import node_snapshots_db as idb

    rows = [{"_row_hash": f"h{i}", "a": i} for i in range(500)]

    with _patched(idb) as (cur, bulk_spy):
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

    # Una sola chiamata batch con 500 record.
    assert bulk_spy.call_count == 1, (
        f"Atteso 1 executemany, trovate {bulk_spy.call_count}: "
        f"{bulk_spy.call_args_list}"
    )
    call = bulk_spy.call_args
    sql = call.args[0]
    records = call.args[1]
    assert "INSERT INTO node_rows" in sql
    assert "VALUES (%s, %s, %s, %s, %s::jsonb)" in sql
    assert len(records) == 500


def test_snapshot_atomic_no_execute_values_when_no_rows():
    """metadata_only / dataset vuoto: niente INSERT batch, solo
    DELETE + UPSERT meta. execute_values non deve essere chiamato."""
    from app.core.intermediate import node_snapshots_db as idb

    with _patched(idb) as (cur, bulk_spy):
        idb.write_node_snapshot_atomic(
            run_id="r1",
            node_id="n1",
            node_type="transform",
            rows_to_persist=[],
            col_schema={"a": "int64"},
            row_count=2000,
            persistence_mode="metadata_only",
        )

    bulk_spy.assert_not_called()
    # DELETE comunque (idempotency)
    assert any(
        "DELETE FROM node_rows" in str(c) for c in cur.execute.call_args_list
    )


# B. write_node_atomic (legacy)


def test_write_node_atomic_legacy_uses_execute_values():
    from app.core.intermediate import node_snapshots_db as idb

    rows = [{"_row_hash": "h1", "a": 1}, {"_row_hash": "h2", "a": 2}]

    with _patched(idb) as (cur, bulk_spy):
        idb.write_node_atomic(
            "rA", "nA",
            node_type="estrai",
            rows=rows,
            col_schema={"a": "int64"},
            exec_time=0.1,
            status="ok",
        )

    assert bulk_spy.call_count == 1
    assert "VALUES (%s, %s, %s, %s, %s::jsonb)" in bulk_spy.call_args.args[0]
    assert len(bulk_spy.call_args.args[1]) == 2


# C. insert_node_rows (legacy idempotent helper)


def test_insert_node_rows_uses_execute_values():
    from app.core.intermediate import node_snapshots_db as idb

    rows = [{"_row_hash": "h1", "a": 1}, {"_row_hash": "h2", "a": 2},
            {"_row_hash": "h3", "a": 3}]

    with _patched(idb) as (cur, bulk_spy):
        idb.insert_node_rows("rB", "nB", rows, {"a": "int64"})

    assert bulk_spy.call_count == 1
    assert "VALUES (%s, %s, %s, %s, %s::jsonb)" in bulk_spy.call_args.args[0]
    assert len(bulk_spy.call_args.args[1]) == 3


# D. Records preservano shape Previous behavior (jsonb-encodable)


def test_records_have_5_tuple_with_jsonb_string_payload():
    """Anti-regression: ogni record passato a execute_values e' una
    tupla 5-ari (run_id, node_id, row_idx, row_hash, jsonb_string).
    `_build_node_row_records` non e' cambiato; il test pinna che
    nessun refactor lo abbia rotto silenziosamente."""
    from app.core.intermediate import node_snapshots_db as idb

    rows = [
        {"_row_hash": "h1", "a": 1, "name": "x"},
        {"_row_hash": "h2", "a": 2, "name": "y"},
    ]

    with _patched(idb) as (_cur, bulk_spy):
        idb.insert_node_rows("rid", "nid", rows, {"a": "int64", "name": "object"})

    records = bulk_spy.call_args.args[1]
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


def test_executemany_uses_single_batch_call():
    """psycopg3 path: una sola chiamata executemany con tutti i record."""
    from app.core.intermediate import node_snapshots_db as idb

    rows = [{"_row_hash": f"h{i}", "a": i} for i in range(50)]

    with _patched(idb) as (_cur, bulk_spy):
        idb.insert_node_rows("r", "n", rows, {"a": "int64"})

    assert bulk_spy.call_count == 1
    assert len(bulk_spy.call_args.args[1]) == 50
