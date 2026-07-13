"""Contratti fail-closed: nessun payload intermedio viene scritto in PG."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.intermediate,
    pytest.mark.contract,
    pytest.mark.regression,
]


def _transaction_spy():
    cursor = MagicMock(name="cursor")
    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cursor
    cursor_cm.__exit__.return_value = False
    connection = MagicMock()
    connection.cursor.return_value = cursor_cm

    @contextmanager
    def transaction():
        yield connection

    return transaction, cursor


def test_snapshot_atomic_rejects_rows_before_opening_transaction():
    from app.core.intermediate import node_snapshots_db as idb

    transaction = MagicMock(side_effect=AssertionError("DB non va aperto"))
    with pytest.raises(ValueError, match="non accetta payload rows"):
        idb.write_node_snapshot_atomic(
            run_id="r1",
            node_id="n1",
            rows_to_persist=[{"a": 1}],
            persistence_mode="full_pg",
            transaction_func=transaction,
        )
    transaction.assert_not_called()


def test_snapshot_atomic_metadata_only_writes_meta_without_node_rows_sql():
    from app.core.intermediate import node_snapshots_db as idb

    transaction, cursor = _transaction_spy()
    idb.write_node_snapshot_atomic(
        run_id="r1",
        node_id="n1",
        rows_to_persist=[],
        row_count=2000,
        persistence_mode="metadata_only",
        transaction_func=transaction,
    )

    cursor.execute.assert_called_once()
    sql = cursor.execute.call_args.args[0]
    assert "INSERT INTO node_meta" in sql
    assert "node_rows" not in sql
    cursor.executemany.assert_not_called()


@pytest.mark.parametrize("helper_name", ["insert_node_rows", "write_node_atomic"])
def test_legacy_postgres_payload_helpers_are_tombstoned(helper_name):
    from app.core.intermediate import node_snapshots_db as idb

    helper = getattr(idb, helper_name)
    with pytest.raises(RuntimeError, match="disabilitato"):
        if helper_name == "insert_node_rows":
            helper("r", "n", [{"a": 1}], {"a": "int"})
        else:
            helper("r", "n", rows=[{"a": 1}])
