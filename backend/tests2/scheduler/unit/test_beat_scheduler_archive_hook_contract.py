"""
Contract tests per gli archive hook del beat scheduler (O-ARC Batch 3/4).

Il file mantiene i comportamenti critici (gate, lock/bypass, invocation,
failure handling e observability) evitando test gemelli logs/audit duplicati.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from workers.beat_scheduler import PlenoraBeatScheduler


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


ARCHIVE_CASES = [
    pytest.param(
        "logs",
        "_archive_old_logs",
        "_last_logs_archive",
        "LOG_RETENTION_DAYS",
        30,
        1_000_000.0,
        "ts",
        "logs/2026-04/logs-2026-04-21-000.ndjson.gz",
        id="logs",
    ),
    pytest.param(
        "audit_actions",
        "_archive_old_audit_actions",
        "_last_audit_archive",
        "AUDIT_RETENTION_DAYS",
        365,
        2_000_000.0,
        "timestamp",
        "audit/2026-04/audit-2026-04-21-000.ndjson.gz",
        id="audit_actions",
    ),
]


def _make_scheduler() -> PlenoraBeatScheduler:
    scheduler = PlenoraBeatScheduler.__new__(PlenoraBeatScheduler)
    scheduler._last_stuck_check = 0.0
    scheduler._last_eseguibili_cleanup = 0.0
    scheduler._last_logs_retention = 0.0
    scheduler._last_logs_archive = 0.0
    scheduler._last_audit_archive = 0.0
    scheduler._last_schedule_logs_retention = 0.0
    scheduler._last_imdf_bundles_cleanup = 0.0
    scheduler._last_temp_orphan_cleanup = 0.0
    return scheduler


def _settings_ns(**overrides) -> SimpleNamespace:
    defaults = dict(
        LOG_ARCHIVE_ENABLED=True,
        LOG_RETENTION_DAYS=30,
        AUDIT_RETENTION_DAYS=365,
        LOG_ARCHIVE_KEY_PREFIX="logs-archive/",
        LOG_ARCHIVE_BATCH_ROWS=10_000,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _writer_result(cutoff_ts: datetime, archived_rows: int = 0, keys=None) -> dict:
    return {
        "archived_rows": archived_rows,
        "object_keys": list(keys or []),
        "cutoff_ts": cutoff_ts,
    }


def _capture_closures(mock_writer: MagicMock) -> dict:
    captured: dict = {}

    def side_effect(table, cutoff_ts, select_rows_cb, delete_cb):
        captured["table"] = table
        captured["cutoff_ts"] = cutoff_ts
        captured["select_rows"] = select_rows_cb
        captured["retention_bypass_execute"] = delete_cb
        return _writer_result(cutoff_ts)

    mock_writer.archive_and_purge.side_effect = side_effect
    return captured


def _call_archive(scheduler: PlenoraBeatScheduler, method_name: str) -> None:
    getattr(scheduler, method_name)()


@pytest.mark.parametrize(
    "table,method_name,slot_name,retention_key,retention_days,"
    "now_ts,_ts_column,object_key",
    ARCHIVE_CASES,
)
def test_archive_hook_invokes_writer_when_enabled(
    monkeypatch,
    table,
    method_name,
    slot_name,
    retention_key,
    retention_days,
    now_ts,
    _ts_column,
    object_key,
):
    scheduler = _make_scheduler()
    monkeypatch.setattr(time, "time", lambda: now_ts)
    mock_storage = MagicMock()

    with patch(
        "app.config.settings",
        new=_settings_ns(LOG_ARCHIVE_ENABLED=True, **{retention_key: retention_days}),
    ), patch(
        "app.core.io.object_storage.get_log_archive_storage",
        return_value=mock_storage,
    ), patch(
        "app.core.scheduler.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.unit_of_work", new=MagicMock(),
    ):
        mock_writer = mock_writer_cls.return_value
        mock_writer.archive_and_purge.return_value = _writer_result(
            datetime.now(timezone.utc),
            archived_rows=42,
            keys=[object_key],
        )

        _call_archive(scheduler, method_name)

    mock_writer_cls.assert_called_once()
    init_kwargs = mock_writer_cls.call_args.kwargs
    assert init_kwargs["storage"] is mock_storage
    assert init_kwargs["key_prefix"] == "logs-archive/"
    assert init_kwargs["batch_size"] == 10_000

    mock_writer.archive_and_purge.assert_called_once()
    call = mock_writer.archive_and_purge.call_args
    assert call.args[0] == table
    cutoff_ts = call.args[1]
    assert cutoff_ts.tzinfo is not None
    expected = datetime.now(timezone.utc) - timedelta(days=retention_days)
    assert abs((cutoff_ts - expected).total_seconds()) <= 1
    assert callable(call.args[2])
    assert callable(call.args[3])
    assert "run_date" not in call.kwargs
    assert getattr(scheduler, slot_name) == now_ts


@pytest.mark.parametrize(
    "method_name,slot_name,settings_overrides",
    [
        pytest.param(
            "_archive_old_logs",
            "_last_logs_archive",
            {"LOG_ARCHIVE_ENABLED": False},
            id="logs-disabled",
        ),
        pytest.param(
            "_archive_old_logs",
            "_last_logs_archive",
            {"LOG_ARCHIVE_ENABLED": True, "LOG_RETENTION_DAYS": 0},
            id="logs-retention-zero",
        ),
        pytest.param(
            "_archive_old_audit_actions",
            "_last_audit_archive",
            {"LOG_ARCHIVE_ENABLED": True, "AUDIT_RETENTION_DAYS": 0},
            id="audit-retention-zero",
        ),
    ],
)
def test_archive_hook_skips_before_writer_on_disabled_or_zero_retention(
    method_name,
    slot_name,
    settings_overrides,
):
    scheduler = _make_scheduler()

    with patch(
        "app.config.settings", new=_settings_ns(**settings_overrides),
    ), patch(
        "app.core.io.object_storage.get_log_archive_storage",
    ) as mock_get_storage, patch(
        "app.core.scheduler.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall",
    ) as mock_fetchall, patch(
        "app.db.pg_connection.unit_of_work",
    ) as mock_transaction:
        _call_archive(scheduler, method_name)

    mock_writer_cls.assert_not_called()
    mock_get_storage.assert_not_called()
    mock_fetchall.assert_not_called()
    mock_transaction.assert_not_called()
    assert getattr(scheduler, slot_name) == 0.0


@pytest.mark.parametrize(
    "table,method_name,_slot_name,_retention_key,_retention_days,"
    "now_ts,_ts_column,_object_key",
    ARCHIVE_CASES,
)
def test_archive_hook_24h_gate(
    monkeypatch,
    table,
    method_name,
    _slot_name,
    _retention_key,
    _retention_days,
    now_ts,
    _ts_column,
    _object_key,
):
    scheduler = _make_scheduler()
    clock = {"now": now_ts}
    monkeypatch.setattr(time, "time", lambda: clock["now"])

    with patch(
        "app.config.settings", new=_settings_ns(),
    ), patch(
        "app.core.io.object_storage.get_log_archive_storage",
        return_value=MagicMock(),
    ), patch(
        "app.core.scheduler.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.unit_of_work", new=MagicMock(),
    ):
        mock_writer = mock_writer_cls.return_value
        mock_writer.archive_and_purge.return_value = _writer_result(
            datetime.now(timezone.utc),
        )

        _call_archive(scheduler, method_name)
        clock["now"] += 60
        _call_archive(scheduler, method_name)
        clock["now"] = now_ts + 86_401
        _call_archive(scheduler, method_name)

    assert mock_writer_cls.call_count == 2
    assert [c.args[0] for c in mock_writer.archive_and_purge.call_args_list] == [
        table,
        table,
    ]


@pytest.mark.parametrize(
    "table,method_name,slot_name,_retention_key,_retention_days,"
    "now_ts,_ts_column,_object_key",
    ARCHIVE_CASES,
)
def test_archive_hook_failure_swallowed_and_slot_not_updated(
    monkeypatch,
    table,
    method_name,
    slot_name,
    _retention_key,
    _retention_days,
    now_ts,
    _ts_column,
    _object_key,
):
    scheduler = _make_scheduler()
    monkeypatch.setattr(time, "time", lambda: now_ts)

    with patch(
        "app.config.settings", new=_settings_ns(),
    ), patch(
        "app.core.io.object_storage.get_log_archive_storage",
        return_value=MagicMock(),
    ), patch(
        "app.core.scheduler.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.unit_of_work", new=MagicMock(),
    ), patch(
        "workers.beat_scheduler.logger",
    ) as mock_logger:
        mock_writer = mock_writer_cls.return_value
        mock_writer.archive_and_purge.side_effect = RuntimeError("S3 down")

        _call_archive(scheduler, method_name)

    mock_logger.warning.assert_called_once()
    warn_call = mock_logger.warning.call_args
    assert f"Errore archive {table}" in warn_call.args[0]
    assert warn_call.kwargs.get("exc_info") is True
    assert getattr(scheduler, slot_name) == 0.0


def test_cleanup_old_logs_skipped_when_archive_enabled():
    scheduler = _make_scheduler()

    with patch(
        "app.config.settings",
        new=_settings_ns(LOG_ARCHIVE_ENABLED=True, LOG_RETENTION_DAYS=30),
    ), patch(
        "app.db.pg_connection.fetchall",
    ) as mock_fetchall, patch(
        "app.db.pg_connection.unit_of_work",
    ) as mock_transaction:
        scheduler._cleanup_old_logs()

    mock_fetchall.assert_not_called()
    mock_transaction.assert_not_called()


def test_cleanup_old_logs_still_runs_when_archive_disabled():
    scheduler = _make_scheduler()
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_conn.cursor.return_value.__exit__.return_value = False
    mock_tx_cm = MagicMock()
    mock_tx_cm.__enter__.return_value = mock_conn
    mock_tx_cm.__exit__.return_value = False

    with patch(
        "app.config.settings",
        new=_settings_ns(LOG_ARCHIVE_ENABLED=False, LOG_RETENTION_DAYS=30),
    ), patch(
        "app.db.pg_connection.fetchall", return_value=[{"c": 7}],
    ) as mock_fetchall, patch(
        "app.db.pg_connection.unit_of_work", return_value=mock_tx_cm,
    ) as mock_transaction:
        scheduler._cleanup_old_logs()

    mock_fetchall.assert_called_once()
    mock_transaction.assert_called_once()
    assert mock_cursor.execute.call_count == 2
    assert "SET LOCAL plenora.retention_cleanup" in (
        mock_cursor.execute.call_args_list[0].args[0]
    )
    delete_call = mock_cursor.execute.call_args_list[1]
    assert "DELETE FROM logs" in delete_call.args[0]
    assert delete_call.args[1] == (30,)


@pytest.mark.parametrize(
    "table,method_name,_slot_name,_retention_key,retention_days,"
    "_now_ts,ts_column,_object_key",
    ARCHIVE_CASES,
)
def test_select_rows_closure_builds_ordered_query(
    table,
    method_name,
    _slot_name,
    _retention_key,
    retention_days,
    _now_ts,
    ts_column,
    _object_key,
):
    scheduler = _make_scheduler()
    mock_fetchall = MagicMock(return_value=[])

    with patch(
        "app.config.settings", new=_settings_ns(),
    ), patch(
        "app.core.io.object_storage.get_log_archive_storage",
        return_value=MagicMock(),
    ), patch(
        "app.core.scheduler.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=mock_fetchall,
    ), patch(
        "app.db.pg_connection.unit_of_work", new=MagicMock(),
    ):
        captured = _capture_closures(mock_writer_cls.return_value)
        _call_archive(scheduler, method_name)
        test_cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        captured["select_rows"](test_cutoff, 10_000)

    sql_arg = mock_fetchall.call_args.args[0]
    params_arg = mock_fetchall.call_args.args[1]
    assert f"FROM {table}" in sql_arg
    assert f"WHERE {ts_column} < %s" in sql_arg
    assert f"ORDER BY {ts_column} ASC, id ASC" in sql_arg
    assert "LIMIT %s" in sql_arg
    assert f"plenora.{table}" not in sql_arg
    assert params_arg == (test_cutoff, 10_000)


@pytest.mark.parametrize(
    "table,method_name,_slot_name,_retention_key,_retention_days,"
    "_now_ts,_ts_column,_object_key",
    ARCHIVE_CASES,
)
def test_retention_bypass_execute_closure_sets_local_flag_on_same_cursor(
    table,
    method_name,
    _slot_name,
    _retention_key,
    _retention_days,
    _now_ts,
    _ts_column,
    _object_key,
):
    scheduler = _make_scheduler()
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_conn.cursor.return_value.__exit__.return_value = False
    mock_tx_cm = MagicMock()
    mock_tx_cm.__enter__.return_value = mock_conn
    mock_tx_cm.__exit__.return_value = False
    mock_transaction = MagicMock(return_value=mock_tx_cm)

    with patch(
        "app.config.settings", new=_settings_ns(),
    ), patch(
        "app.core.io.object_storage.get_log_archive_storage",
        return_value=MagicMock(),
    ), patch(
        "app.core.scheduler.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.unit_of_work", new=mock_transaction,
    ):
        captured = _capture_closures(mock_writer_cls.return_value)
        _call_archive(scheduler, method_name)
        captured["retention_bypass_execute"](
            f"DELETE FROM plenora.{table} WHERE id <= %s",
            (123,),
        )

    mock_transaction.assert_called_once()
    assert mock_cursor.execute.call_count == 2
    assert "SET LOCAL plenora.retention_cleanup" in (
        mock_cursor.execute.call_args_list[0].args[0]
    )
    delete_call = mock_cursor.execute.call_args_list[1]
    assert f"DELETE FROM plenora.{table}" in delete_call.args[0]
    assert delete_call.args[1] == (123,)


@pytest.mark.parametrize(
    "table,method_name,_slot_name,retention_key,retention_days,"
    "now_ts,_ts_column,object_key",
    ARCHIVE_CASES,
)
def test_archive_hook_emits_observability_insert_on_success(
    monkeypatch,
    table,
    method_name,
    _slot_name,
    retention_key,
    retention_days,
    now_ts,
    _ts_column,
    object_key,
):
    scheduler = _make_scheduler()
    monkeypatch.setattr(time, "time", lambda: now_ts)
    mock_pg_execute = MagicMock()

    with patch(
        "app.config.settings",
        new=_settings_ns(LOG_ARCHIVE_ENABLED=True, **{retention_key: retention_days}),
    ), patch(
        "app.core.io.object_storage.get_log_archive_storage",
        return_value=MagicMock(),
    ), patch(
        "app.core.scheduler.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.unit_of_work", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.execute", new=mock_pg_execute,
    ):
        mock_writer_cls.return_value.archive_and_purge.return_value = _writer_result(
            datetime.now(timezone.utc),
            archived_rows=42,
            keys=[object_key],
        )
        _call_archive(scheduler, method_name)

    mock_pg_execute.assert_called_once()
    sql_arg = mock_pg_execute.call_args.args[0]
    params_arg = mock_pg_execute.call_args.args[1]
    assert "INSERT INTO logs" in sql_arg
    assert "ts, level, module, op, msg" in sql_arg
    assert "project_id, group_id, run_id, node_id" in sql_arg
    assert "details, error" in sql_arg
    assert sql_arg.count("%s") == 11
    assert isinstance(params_arg, tuple)
    assert len(params_arg) == 11

    (ts, level, module, op, msg, project_id, group_id,
     run_id, node_id, details, error) = params_arg
    assert ts and isinstance(ts, str)
    assert level == "INFO"
    assert module == "workers"
    assert op == "log_archive"
    assert f"Archive {table}: 42 righe" in msg
    assert "1 oggetti" in msg
    assert (project_id, group_id, run_id, node_id, error) == ("", "", "", "", "")

    details_obj = json.loads(details)
    assert set(details_obj.keys()) == {
        "table", "cutoff", "archived_rows", "object_keys", "batch_runs",
    }
    assert details_obj["table"] == table
    assert details_obj["archived_rows"] == 42
    assert details_obj["batch_runs"] == 1
    assert details_obj["object_keys"] == [object_key]


def test_archive_hook_skips_observability_when_gate_early_returns():
    scheduler = _make_scheduler()
    mock_pg_execute = MagicMock()

    with patch(
        "app.config.settings", new=_settings_ns(LOG_ARCHIVE_ENABLED=False),
    ), patch(
        "app.core.io.object_storage.get_log_archive_storage",
    ), patch(
        "app.core.scheduler.log_archive.LogArchiveWriter",
    ), patch(
        "app.db.pg_connection.execute", new=mock_pg_execute,
    ):
        scheduler._archive_old_logs()

    with patch(
        "app.config.settings",
        new=_settings_ns(LOG_ARCHIVE_ENABLED=True, AUDIT_RETENTION_DAYS=0),
    ), patch(
        "app.core.io.object_storage.get_log_archive_storage",
    ), patch(
        "app.core.scheduler.log_archive.LogArchiveWriter",
    ), patch(
        "app.db.pg_connection.execute", new=mock_pg_execute,
    ):
        scheduler._archive_old_audit_actions()

    mock_pg_execute.assert_not_called()


def test_archive_old_logs_skips_observability_when_archive_fails(monkeypatch):
    scheduler = _make_scheduler()
    monkeypatch.setattr(time, "time", lambda: 1_000_000.0)
    mock_pg_execute = MagicMock()

    with patch(
        "app.config.settings", new=_settings_ns(),
    ), patch(
        "app.core.io.object_storage.get_log_archive_storage",
        return_value=MagicMock(),
    ), patch(
        "app.core.scheduler.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.unit_of_work", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.execute", new=mock_pg_execute,
    ):
        mock_writer_cls.return_value.archive_and_purge.side_effect = RuntimeError(
            "S3 down"
        )
        scheduler._archive_old_logs()

    mock_pg_execute.assert_not_called()
    assert scheduler._last_logs_archive == 0.0


def test_archive_old_logs_observability_failure_does_not_regress_archive_ok(
    monkeypatch,
):
    scheduler = _make_scheduler()
    now_ts = 1_000_000.0
    monkeypatch.setattr(time, "time", lambda: now_ts)
    mock_pg_execute = MagicMock(side_effect=RuntimeError("db transient"))

    with patch(
        "app.config.settings", new=_settings_ns(),
    ), patch(
        "app.core.io.object_storage.get_log_archive_storage",
        return_value=MagicMock(),
    ), patch(
        "app.core.scheduler.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.unit_of_work", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.execute", new=mock_pg_execute,
    ), patch(
        "workers.beat_scheduler.logger",
    ) as mock_logger:
        mock_writer_cls.return_value.archive_and_purge.return_value = _writer_result(
            datetime.now(timezone.utc),
            archived_rows=5,
        )
        scheduler._archive_old_logs()

    assert scheduler._last_logs_archive == now_ts
    mock_pg_execute.assert_called_once()
    warning_calls = [
        c for c in mock_logger.warning.call_args_list
        if "Observability log_archive fallita" in c.args[0]
    ]
    assert len(warning_calls) == 1
