"""Idempotenza corrente: upsert dei metadati, zero payload PostgreSQL."""

from __future__ import annotations

from unittest.mock import patch

import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.intermediate,
    pytest.mark.contract,
    pytest.mark.regression,
]


def test_writer_success_uses_extended_meta_upsert_only():
    from app.core.intermediate import writer as w

    rows = [{"col_a": 1}, {"col_a": 2}]
    with patch.object(
        w.node_snapshots_db,
        "upsert_node_meta_extended",
    ) as meta_upsert, patch.object(
        w.node_snapshots_db,
        "insert_node_rows",
    ) as row_insert:
        result = w.write_node(
            run_id="run-r",
            node_id="node-r",
            node_type="estrai",
            rows=rows,
            col_schema={"col_a": "int64"},
            exec_time=0.4,
            status="ok",
        )

    assert len(result) == 64
    meta_upsert.assert_called_once()
    kwargs = meta_upsert.call_args.kwargs
    assert kwargs["row_count"] == 2
    assert kwargs["persistence_mode"] == "metadata_only"
    assert kwargs["persisted_rows_count"] == 0
    row_insert.assert_not_called()


def test_writer_repeated_calls_are_meta_upserts_without_row_writes():
    from app.core.intermediate import writer as w

    with patch.object(
        w.node_snapshots_db,
        "upsert_node_meta_extended",
    ) as meta_upsert, patch.object(
        w.node_snapshots_db,
        "insert_node_rows",
    ) as row_insert:
        for rows in ([{"a": 1}], [{"a": 2}, {"a": 3}]):
            w.write_node(
                run_id="run-r",
                node_id="node-r",
                node_type="estrai",
                rows=rows,
                col_schema={"a": "int64"},
                exec_time=0.1,
                status="ok",
            )

    assert meta_upsert.call_count == 2
    assert [call.kwargs["row_count"] for call in meta_upsert.call_args_list] == [1, 2]
    row_insert.assert_not_called()


def test_writer_empty_and_error_paths_remain_metadata_only():
    from app.core.intermediate import writer as w

    with patch.object(
        w.node_snapshots_db,
        "upsert_node_meta_extended",
    ) as meta_upsert:
        w.write_node(
            run_id="run-empty",
            node_id="node-empty",
            node_type="estrai",
            rows=[],
            col_schema={"a": "int64"},
            exec_time=0.1,
            status="ok",
        )
        w.write_node(
            run_id="run-error",
            node_id="node-error",
            node_type="estrai",
            rows=None,
            col_schema={},
            exec_time=0.1,
            status="error",
            error_msg="boom",
        )

    assert meta_upsert.call_count == 2
    assert all(
        call.kwargs["persistence_mode"] == "metadata_only"
        and call.kwargs["persisted_rows_count"] == 0
        for call in meta_upsert.call_args_list
    )


@pytest.mark.parametrize("helper_name", ["insert_node_rows", "write_node_atomic"])
def test_legacy_payload_writers_fail_closed(helper_name):
    from app.core.intermediate import node_snapshots_db as idb

    helper = getattr(idb, helper_name)
    with pytest.raises(RuntimeError, match="disabilitato"):
        if helper_name == "insert_node_rows":
            helper("run", "node", [], {})
        else:
            helper("run", "node", rows=[])
