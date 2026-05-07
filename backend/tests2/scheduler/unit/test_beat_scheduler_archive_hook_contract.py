"""
Contract tests per gli archive hook del beat scheduler (O-ARC Batch 3).

Pattern mock-based, clonato da test_beat_scheduler_cleanup_contract.py.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from workers.beat_scheduler import PlenoraBeatScheduler


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


# Helpers


def _make_scheduler() -> PlenoraBeatScheduler:
    """Scheduler bypass __init__ con tutti gli slot time-gate azzerati.

    Clona _make_scheduler di test_beat_scheduler_cleanup_contract.py e
    aggiunge i due slot Batch 3 (_last_logs_archive, _last_audit_archive).
    """
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
    """SimpleNamespace con i default dei test archive hook.

    Default = config "enabled + coerente". I test che simulano
    disabled / retention=0 / altri edge case sovrascrivono via kwargs.
    Nessuna dipendenza dalla classe Settings reale: cosi' i test
    restano indipendenti da config.py.
    """
    defaults = dict(
        LOG_ARCHIVE_ENABLED=True,
        LOG_RETENTION_DAYS=30,
        AUDIT_RETENTION_DAYS=365,
        LOG_ARCHIVE_KEY_PREFIX="logs-archive/",
        LOG_ARCHIVE_BATCH_ROWS=10_000,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _writer_result(
    cutoff_ts: datetime,
    archived_rows: int = 0,
    keys=None,
) -> dict:
    """Shape minima di ArchiveResult TypedDict (O-ARC Batch 1)."""
    return {
        "archived_rows": archived_rows,
        "object_keys": list(keys or []),
        "cutoff_ts": cutoff_ts,
    }


def _capture_closures(mock_writer: MagicMock) -> dict:
    """Installa uno `side_effect` che cattura gli argomenti passati a
    `archive_and_purge`. Il dict ritornato e' mutato in loco dal
    side_effect quando il writer viene invocato dal hook.
    """
    captured: dict = {}

    def side_effect(table, cutoff_ts, select_rows_cb, delete_cb):
        captured["table"] = table
        captured["cutoff_ts"] = cutoff_ts
        captured["select_rows"] = select_rows_cb
        captured["retention_bypass_execute"] = delete_cb
        return _writer_result(cutoff_ts)

    mock_writer.archive_and_purge.side_effect = side_effect
    return captured


# Section 1 — _archive_old_logs hook (B3-T1..T5)


def test_archive_old_logs_invokes_writer_when_enabled(monkeypatch):
    """B3-T1: happy path.

    ENABLED=True + LOG_RETENTION_DAYS=30 -> writer.archive_and_purge
    chiamato 1 volta con table='logs', cutoff tz-aware = now-30gg
    (tolleranza ±1s), 2 callables non-None. Slot aggiornato.
    """
    scheduler = _make_scheduler()
    now_ts = 1_000_000.0
    monkeypatch.setattr(time, "time", lambda: now_ts)

    mock_storage = MagicMock()
    with patch(
        "app.config.settings",
        new=_settings_ns(LOG_ARCHIVE_ENABLED=True, LOG_RETENTION_DAYS=30),
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
        return_value=mock_storage,
    ), patch(
        "app.services.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.transaction", new=MagicMock(),
    ):
        mock_writer = mock_writer_cls.return_value
        mock_writer.archive_and_purge.return_value = _writer_result(
            datetime.now(timezone.utc),
            archived_rows=42,
            keys=["logs/2026-04/logs-2026-04-21-000.ndjson.gz"],
        )

        scheduler._archive_old_logs()

        # Writer istanziato una volta con i parametri da settings
        assert mock_writer_cls.call_count == 1
        init_kwargs = mock_writer_cls.call_args.kwargs
        assert init_kwargs["storage"] is mock_storage
        assert init_kwargs["key_prefix"] == "logs-archive/"
        assert init_kwargs["batch_size"] == 10_000

        # archive_and_purge chiamato con table='logs' e cutoff corretto
        assert mock_writer.archive_and_purge.call_count == 1
        call = mock_writer.archive_and_purge.call_args

    assert call.args[0] == "logs"
    cutoff_ts = call.args[1]
    assert cutoff_ts.tzinfo is not None, "cutoff must be tz-aware"
    expected = datetime.now(timezone.utc) - timedelta(days=30)
    delta_sec = abs((cutoff_ts - expected).total_seconds())
    assert delta_sec <= 1, f"cutoff drift {delta_sec}s (expected ±1s)"
    assert callable(call.args[2])  # select_rows
    assert callable(call.args[3])  # retention_bypass_execute
    # run_date default (None nel writer) -> non passato esplicitamente
    assert "run_date" not in call.kwargs

    # Slot aggiornato a now (gate 24h scattera' al prossimo tick)
    assert scheduler._last_logs_archive == now_ts


def test_archive_old_logs_skips_when_disabled():
    """B3-T2: LOG_ARCHIVE_ENABLED=False -> no-op completo.

    Writer non istanziato, factory storage non richiesta, slot
    invariato. Nessun accesso a fetchall/transaction.
    """
    scheduler = _make_scheduler()

    with patch(
        "app.config.settings", new=_settings_ns(LOG_ARCHIVE_ENABLED=False),
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
    ) as mock_get_storage, patch(
        "app.services.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall",
    ) as mock_fetchall, patch(
        "app.db.pg_connection.transaction",
    ) as mock_transaction:
        scheduler._archive_old_logs()

    mock_writer_cls.assert_not_called()
    mock_get_storage.assert_not_called()
    mock_fetchall.assert_not_called()
    mock_transaction.assert_not_called()
    assert scheduler._last_logs_archive == 0.0


def test_archive_old_logs_skips_when_retention_zero():
    """B3-T3: ENABLED=True + LOG_RETENTION_DAYS=0 -> writer non
    istanziato. Simmetrico al baseline _cleanup_old_logs con retention=0.
    """
    scheduler = _make_scheduler()

    with patch(
        "app.config.settings",
        new=_settings_ns(LOG_ARCHIVE_ENABLED=True, LOG_RETENTION_DAYS=0),
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
    ) as mock_get_storage, patch(
        "app.services.log_archive.LogArchiveWriter",
    ) as mock_writer_cls:
        scheduler._archive_old_logs()

    mock_writer_cls.assert_not_called()
    mock_get_storage.assert_not_called()
    assert scheduler._last_logs_archive == 0.0


def test_archive_old_logs_24h_gate(monkeypatch):
    """B3-T4: dopo un run success, la chiamata +60s e' no-op;
    dopo +86401s rientra.
    """
    scheduler = _make_scheduler()
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(time, "time", lambda: clock["now"])

    with patch(
        "app.config.settings", new=_settings_ns(),
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
        return_value=MagicMock(),
    ), patch(
        "app.services.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.transaction", new=MagicMock(),
    ):
        mock_writer = mock_writer_cls.return_value
        mock_writer.archive_and_purge.return_value = _writer_result(
            datetime.now(timezone.utc),
        )

        scheduler._archive_old_logs()
        assert mock_writer_cls.call_count == 1

        # +60s: 24h gate blocca
        clock["now"] += 60
        scheduler._archive_old_logs()
        assert mock_writer_cls.call_count == 1, "gate 24h non ha bloccato"

        # +86401s dal primo run: gate consente un nuovo run
        clock["now"] = 1_000_000.0 + 86_401
        scheduler._archive_old_logs()
        assert mock_writer_cls.call_count == 2


def test_archive_old_logs_failure_swallowed(monkeypatch):
    """B3-T5: writer solleva RuntimeError -> il metodo non propaga,
    logger.warning(exc_info=True) chiamato, slot NON aggiornato
    (retry al prossimo tick gate-elapsed).
    """
    scheduler = _make_scheduler()
    now_ts = 1_000_000.0
    monkeypatch.setattr(time, "time", lambda: now_ts)

    with patch(
        "app.config.settings", new=_settings_ns(),
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
        return_value=MagicMock(),
    ), patch(
        "app.services.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.transaction", new=MagicMock(),
    ), patch(
        "workers.beat_scheduler.logger",
    ) as mock_logger:
        mock_writer = mock_writer_cls.return_value
        mock_writer.archive_and_purge.side_effect = RuntimeError("S3 down")

        scheduler._archive_old_logs()  # must not raise

    mock_logger.warning.assert_called_once()
    warn_call = mock_logger.warning.call_args
    assert "Errore archive logs" in warn_call.args[0]
    assert warn_call.kwargs.get("exc_info") is True
    # Slot NON aggiornato -> retry al prossimo tick
    assert scheduler._last_logs_archive == 0.0


# Section 2 — _cleanup_old_logs gate (B3-T6..T7)


def test_cleanup_old_logs_skipped_when_archive_enabled():
    """B3-T6: ENABLED=True -> _cleanup_old_logs fa early return.

    Nessuna DELETE legacy (protezione double-DELETE: il path di purge
    canonico e' gia' in _archive_old_logs, invocato prima nel tick).
    """
    scheduler = _make_scheduler()

    with patch(
        "app.config.settings",
        new=_settings_ns(LOG_ARCHIVE_ENABLED=True, LOG_RETENTION_DAYS=30),
    ), patch(
        "app.db.pg_connection.fetchall",
    ) as mock_fetchall, patch(
        "app.db.pg_connection.transaction",
    ) as mock_transaction:
        scheduler._cleanup_old_logs()

    mock_fetchall.assert_not_called()
    mock_transaction.assert_not_called()


def test_cleanup_old_logs_still_runs_when_archive_disabled():
    """B3-T7: ENABLED=False + LOG_RETENTION_DAYS=30 -> path legacy
    invariato (fetchall COUNT + transaction + SET LOCAL + DELETE sullo
    stesso cursor). Replica esplicita del pattern Batch 2.
    """
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
        "app.db.pg_connection.transaction", return_value=mock_tx_cm,
    ) as mock_transaction:
        scheduler._cleanup_old_logs()

    mock_fetchall.assert_called_once()
    mock_transaction.assert_called_once()
    # Sullo stesso cursor: prima SET LOCAL, poi DELETE
    assert mock_cursor.execute.call_count == 2
    set_local_call = mock_cursor.execute.call_args_list[0]
    assert "SET LOCAL plenora.retention_cleanup" in set_local_call.args[0]
    assert "'true'" in set_local_call.args[0]
    delete_call = mock_cursor.execute.call_args_list[1]
    assert "DELETE FROM logs" in delete_call.args[0]
    assert delete_call.args[1] == (30,)


# Section 3 — _archive_old_audit_actions hook (B3-T8..T10)


def test_archive_old_audit_actions_invokes_writer_when_enabled(monkeypatch):
    """B3-T8: ENABLED=True + AUDIT_RETENTION_DAYS=365 -> writer chiamato
    con table='audit_actions', cutoff tz-aware = now-365gg (±1s),
    2 callables. Slot _last_audit_archive aggiornato.
    """
    scheduler = _make_scheduler()
    now_ts = 2_000_000.0
    monkeypatch.setattr(time, "time", lambda: now_ts)

    mock_storage = MagicMock()
    with patch(
        "app.config.settings",
        new=_settings_ns(LOG_ARCHIVE_ENABLED=True, AUDIT_RETENTION_DAYS=365),
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
        return_value=mock_storage,
    ), patch(
        "app.services.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.transaction", new=MagicMock(),
    ):
        mock_writer = mock_writer_cls.return_value
        mock_writer.archive_and_purge.return_value = _writer_result(
            datetime.now(timezone.utc), archived_rows=7,
        )

        scheduler._archive_old_audit_actions()

        assert mock_writer_cls.call_count == 1
        init_kwargs = mock_writer_cls.call_args.kwargs
        assert init_kwargs["storage"] is mock_storage
        assert init_kwargs["key_prefix"] == "logs-archive/"
        assert init_kwargs["batch_size"] == 10_000

        assert mock_writer.archive_and_purge.call_count == 1
        call = mock_writer.archive_and_purge.call_args

    assert call.args[0] == "audit_actions"
    cutoff_ts = call.args[1]
    assert cutoff_ts.tzinfo is not None
    expected = datetime.now(timezone.utc) - timedelta(days=365)
    delta_sec = abs((cutoff_ts - expected).total_seconds())
    assert delta_sec <= 1, f"cutoff drift {delta_sec}s (expected ±1s)"
    assert callable(call.args[2])
    assert callable(call.args[3])
    assert "run_date" not in call.kwargs
    assert scheduler._last_audit_archive == now_ts


def test_archive_old_audit_actions_skips_when_retention_zero():
    """B3-T9: AUDIT_RETENTION_DAYS=0 -> writer non istanziato.

    Protegge da infinite-loop: con cutoff=now il writer itererebbe
    finche' SELECT svuota la tabella. Il gate lo esclude a monte.
    """
    scheduler = _make_scheduler()

    with patch(
        "app.config.settings",
        new=_settings_ns(LOG_ARCHIVE_ENABLED=True, AUDIT_RETENTION_DAYS=0),
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
    ) as mock_get_storage, patch(
        "app.services.log_archive.LogArchiveWriter",
    ) as mock_writer_cls:
        scheduler._archive_old_audit_actions()

    mock_writer_cls.assert_not_called()
    mock_get_storage.assert_not_called()
    assert scheduler._last_audit_archive == 0.0


def test_archive_old_audit_actions_24h_gate(monkeypatch):
    """B3-T10: gemello di T4 sullo slot _last_audit_archive."""
    scheduler = _make_scheduler()
    clock = {"now": 2_000_000.0}
    monkeypatch.setattr(time, "time", lambda: clock["now"])

    with patch(
        "app.config.settings", new=_settings_ns(),
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
        return_value=MagicMock(),
    ), patch(
        "app.services.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.transaction", new=MagicMock(),
    ):
        mock_writer = mock_writer_cls.return_value
        mock_writer.archive_and_purge.return_value = _writer_result(
            datetime.now(timezone.utc),
        )

        scheduler._archive_old_audit_actions()
        assert mock_writer_cls.call_count == 1

        clock["now"] += 60
        scheduler._archive_old_audit_actions()
        assert mock_writer_cls.call_count == 1, "gate 24h non ha bloccato"

        clock["now"] = 2_000_000.0 + 86_401
        scheduler._archive_old_audit_actions()
        assert mock_writer_cls.call_count == 2


# Section 4 — closure contract verso il writer (B3-T11..T12)


def test_select_rows_closure_builds_ordered_query_for_logs():
    """B3-T11: la closure select_rows passata al writer per logs
    esegue fetchall con `FROM logs`, `WHERE ts < %s`,
    `ORDER BY ts ASC, id ASC`, `LIMIT %s` e params (cutoff, N).
    """
    scheduler = _make_scheduler()
    mock_fetchall = MagicMock(return_value=[])

    with patch(
        "app.config.settings", new=_settings_ns(),
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
        return_value=MagicMock(),
    ), patch(
        "app.services.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=mock_fetchall,
    ), patch(
        "app.db.pg_connection.transaction", new=MagicMock(),
    ):
        mock_writer = mock_writer_cls.return_value
        captured = _capture_closures(mock_writer)

        scheduler._archive_old_logs()
        assert "select_rows" in captured, "writer.archive_and_purge non chiamato"

        test_cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        captured["select_rows"](test_cutoff, 10_000)

    mock_fetchall.assert_called_once()
    sql_arg = mock_fetchall.call_args.args[0]
    params_arg = mock_fetchall.call_args.args[1]
    assert "FROM logs" in sql_arg
    assert "WHERE ts < %s" in sql_arg
    assert "ORDER BY ts ASC, id ASC" in sql_arg
    assert "LIMIT %s" in sql_arg
    # Schema-agnostic: nessun prefisso `plenora.` lato SELECT
    assert "plenora.logs" not in sql_arg
    assert params_arg == (test_cutoff, 10_000)


def test_select_rows_closure_builds_ordered_query_for_audit_actions():
    """B3-T11 (gemello): stesso contratto su audit_actions con ts col
    = 'timestamp' (O-ARC assunzione #4).
    """
    scheduler = _make_scheduler()
    mock_fetchall = MagicMock(return_value=[])

    with patch(
        "app.config.settings", new=_settings_ns(),
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
        return_value=MagicMock(),
    ), patch(
        "app.services.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=mock_fetchall,
    ), patch(
        "app.db.pg_connection.transaction", new=MagicMock(),
    ):
        mock_writer = mock_writer_cls.return_value
        captured = _capture_closures(mock_writer)

        scheduler._archive_old_audit_actions()
        assert "select_rows" in captured

        test_cutoff = datetime.now(timezone.utc) - timedelta(days=365)
        captured["select_rows"](test_cutoff, 10_000)

    mock_fetchall.assert_called_once()
    sql_arg = mock_fetchall.call_args.args[0]
    params_arg = mock_fetchall.call_args.args[1]
    assert "FROM audit_actions" in sql_arg
    assert "WHERE timestamp < %s" in sql_arg
    assert "ORDER BY timestamp ASC, id ASC" in sql_arg
    assert "LIMIT %s" in sql_arg
    assert "plenora.audit_actions" not in sql_arg
    assert params_arg == (test_cutoff, 10_000)


def test_retention_bypass_execute_closure_sets_local_flag_on_same_cursor():
    """B3-T12: la closure retention_bypass_execute apre `transaction()`,
    esegue `SET LOCAL plenora.retention_cleanup = 'true'` e la DELETE
    sullo stesso cursor (2 execute esatte, in ordine).

    Speculare al pattern di `_cleanup_old_logs`: se fossero due cursor
    o due tx distinte, il trigger `plenora.block_log_modifications`
    vedrebbe il flag solo nel primo scope e scatterebbe sulla DELETE.
    """
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
        "app.shared.cloud_backends.get_log_archive_storage",
        return_value=MagicMock(),
    ), patch(
        "app.services.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.transaction", new=mock_transaction,
    ):
        mock_writer = mock_writer_cls.return_value
        captured = _capture_closures(mock_writer)

        scheduler._archive_old_logs()
        assert "retention_bypass_execute" in captured

        # Invoco la closure con (sql, params) sentinella
        captured["retention_bypass_execute"](
            "DELETE FROM plenora.logs WHERE ts < %s AND id <= %s",
            ("cutoff_sentinel", 123),
        )

    mock_transaction.assert_called_once()
    # Stesso cursor: 2 execute (SET LOCAL + DELETE), in ordine
    assert mock_cursor.execute.call_count == 2
    set_local_call = mock_cursor.execute.call_args_list[0]
    assert "SET LOCAL plenora.retention_cleanup" in set_local_call.args[0]
    assert "'true'" in set_local_call.args[0]
    delete_call = mock_cursor.execute.call_args_list[1]
    assert "DELETE FROM plenora.logs" in delete_call.args[0]
    assert delete_call.args[1] == ("cutoff_sentinel", 123)


# Section 5 — B4 observability circolare (ADR-0014 §B)
#
# Verifica che al termine di un archive successo venga emessa una riga
# INFO su plenora.logs via pg_execute diretta. LOG_LEVEL_DB=WARNING
# default filtra logger.info() prima del PostgresLogHandler, quindi la
# chiusura del cerchio osservabilita' richiede INSERT esplicita.
# Shape 11-col identica al handler canonico (utils/logger.py:340-344).
# Append-only preservato: INSERT passa dal trigger
# plenora.block_log_modifications senza bypass (solo UPDATE/DELETE
# bloccati). Best-effort: fallimento observability non regredisce
# lo stato "archive ok" (slot gia' aggiornato prima della INSERT).


def test_archive_old_logs_emits_observability_insert_on_success(monkeypatch):
    """B4-T1 logs: shape INSERT esplicita.

    Verifica:
    - pg_execute chiamato 1 volta dopo archive success
    - SQL = 11-col INSERT INTO logs, 11 placeholder
    - valori: ts non-vuoto, level='INFO', module='workers',
      op='log_archive', msg contiene 'Archive logs: 42 righe'
    - project_id/group_id/run_id/node_id tutti ''
    - details JSON parseable con 5 chiavi, table='logs',
      archived_rows=42, batch_runs=1
    - error=''
    """
    import json

    scheduler = _make_scheduler()
    now_ts = 1_000_000.0
    monkeypatch.setattr(time, "time", lambda: now_ts)

    mock_pg_execute = MagicMock()

    with patch(
        "app.config.settings",
        new=_settings_ns(LOG_ARCHIVE_ENABLED=True, LOG_RETENTION_DAYS=30),
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
        return_value=MagicMock(),
    ), patch(
        "app.services.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.transaction", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.execute", new=mock_pg_execute,
    ):
        mock_writer = mock_writer_cls.return_value
        mock_writer.archive_and_purge.return_value = _writer_result(
            datetime.now(timezone.utc),
            archived_rows=42,
            keys=["logs/2026-04/logs-2026-04-21-000.ndjson.gz"],
        )

        scheduler._archive_old_logs()

    assert mock_pg_execute.call_count == 1, (
        "observability INSERT non emessa dopo archive success"
    )
    sql_arg = mock_pg_execute.call_args.args[0]
    params_arg = mock_pg_execute.call_args.args[1]

    # Shape SQL: 11 colonne, 11 placeholder
    assert "INSERT INTO logs" in sql_arg
    assert "ts, level, module, op, msg" in sql_arg
    assert "project_id, group_id, run_id, node_id" in sql_arg
    assert "details, error" in sql_arg
    assert sql_arg.count("%s") == 11, (
        f"SQL deve avere 11 placeholder, trovati {sql_arg.count('%s')}"
    )

    # Tuple di 11 valori
    assert isinstance(params_arg, tuple)
    assert len(params_arg) == 11, (
        f"params deve essere tuple(11), trovato len={len(params_arg)}"
    )

    (ts, level, module, op, msg, project_id, group_id,
     run_id, node_id, details, error) = params_arg

    assert ts and isinstance(ts, str), "ts deve essere str non-vuoto"
    assert level == "INFO"
    assert module == "workers"
    assert op == "log_archive"
    assert "Archive logs: 42 righe" in msg
    assert "1 oggetti" in msg
    assert project_id == ""
    assert group_id == ""
    assert run_id == ""
    assert node_id == ""
    assert error == ""

    # details = JSON con 5 chiavi canoniche, table='logs'
    details_obj = json.loads(details)
    assert set(details_obj.keys()) == {
        "table", "cutoff", "archived_rows", "object_keys", "batch_runs",
    }
    assert details_obj["table"] == "logs"
    assert details_obj["archived_rows"] == 42
    assert details_obj["batch_runs"] == 1
    assert details_obj["object_keys"] == [
        "logs/2026-04/logs-2026-04-21-000.ndjson.gz"
    ]


def test_archive_old_audit_actions_emits_observability_insert_on_success(
    monkeypatch,
):
    """B4-T1 gemello audit_actions: shape INSERT esplicita.

    Stessa struttura 11-col, ma details.table='audit_actions' e msg
    inizia con 'Archive audit_actions:'. La riga finisce comunque
    in plenora.logs (chiusura del cerchio log-del-log su un'unica
    tabella osservabile).
    """
    import json

    scheduler = _make_scheduler()
    now_ts = 2_000_000.0
    monkeypatch.setattr(time, "time", lambda: now_ts)

    mock_pg_execute = MagicMock()

    with patch(
        "app.config.settings",
        new=_settings_ns(LOG_ARCHIVE_ENABLED=True, AUDIT_RETENTION_DAYS=365),
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
        return_value=MagicMock(),
    ), patch(
        "app.services.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.transaction", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.execute", new=mock_pg_execute,
    ):
        mock_writer = mock_writer_cls.return_value
        mock_writer.archive_and_purge.return_value = _writer_result(
            datetime.now(timezone.utc),
            archived_rows=7,
            keys=["audit/2026-04/audit-2026-04-21-000.ndjson.gz"],
        )

        scheduler._archive_old_audit_actions()

    assert mock_pg_execute.call_count == 1
    sql_arg = mock_pg_execute.call_args.args[0]
    params_arg = mock_pg_execute.call_args.args[1]

    # INSERT va SEMPRE su plenora.logs (non su audit_actions): la
    # chiusura del cerchio osservabilita' e' unica per entrambe le
    # pipeline archive.
    assert "INSERT INTO logs" in sql_arg
    assert sql_arg.count("%s") == 11
    assert len(params_arg) == 11

    (_ts, level, module, op, msg, project_id, _group_id,
     _run_id, _node_id, details, error) = params_arg

    assert level == "INFO"
    assert module == "workers"
    assert op == "log_archive"
    assert "Archive audit_actions:" in msg
    assert "7 righe" in msg
    assert project_id == ""
    assert error == ""

    details_obj = json.loads(details)
    assert details_obj["table"] == "audit_actions"
    assert details_obj["archived_rows"] == 7
    assert details_obj["batch_runs"] == 1


def test_archive_old_logs_skips_observability_when_gate_early_returns():
    """B4-T2: i gate early-return (ENABLED=False, retention=0) non
    devono emettere observability.

    Due scenari in un solo test: entrambi i hook con ENABLED=False
    non invocano pg_execute. Completa la semantica di B3-T2/B3-T9
    chiudendo il contratto anche sul path osservabilita'.
    """
    scheduler = _make_scheduler()
    mock_pg_execute = MagicMock()

    # Scenario A: _archive_old_logs con ENABLED=False
    with patch(
        "app.config.settings", new=_settings_ns(LOG_ARCHIVE_ENABLED=False),
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
    ), patch(
        "app.services.log_archive.LogArchiveWriter",
    ), patch(
        "app.db.pg_connection.execute", new=mock_pg_execute,
    ):
        scheduler._archive_old_logs()

    assert mock_pg_execute.call_count == 0, (
        "observability non deve essere emessa con ENABLED=False (logs)"
    )

    # Scenario B: _archive_old_audit_actions con AUDIT_RETENTION_DAYS=0
    with patch(
        "app.config.settings",
        new=_settings_ns(
            LOG_ARCHIVE_ENABLED=True, AUDIT_RETENTION_DAYS=0,
        ),
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
    ), patch(
        "app.services.log_archive.LogArchiveWriter",
    ), patch(
        "app.db.pg_connection.execute", new=mock_pg_execute,
    ):
        scheduler._archive_old_audit_actions()

    assert mock_pg_execute.call_count == 0, (
        "observability non deve essere emessa con retention=0 (audit)"
    )


def test_archive_old_logs_skips_observability_when_archive_fails(monkeypatch):
    """B4-T3: se writer.archive_and_purge solleva, observability non
    viene emessa.

    Rationale ADR-0014 §B3: observability = success-only. Un fallimento
    archive non deve contaminare plenora.logs con una INSERT "finta"
    di success. L'handler canonico (utils/logger.py) traccia gia'
    l'error via logger.warning(exc_info=True) -> se WARNING passa
    il level filter, finisce comunque in plenora.logs come riga error.
    """
    scheduler = _make_scheduler()
    now_ts = 1_000_000.0
    monkeypatch.setattr(time, "time", lambda: now_ts)

    mock_pg_execute = MagicMock()

    with patch(
        "app.config.settings", new=_settings_ns(),
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
        return_value=MagicMock(),
    ), patch(
        "app.services.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.transaction", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.execute", new=mock_pg_execute,
    ):
        mock_writer = mock_writer_cls.return_value
        mock_writer.archive_and_purge.side_effect = RuntimeError("S3 down")

        scheduler._archive_old_logs()  # must not raise

    assert mock_pg_execute.call_count == 0, (
        "observability success-only: nessuna INSERT su archive failure"
    )
    # Slot NON aggiornato -> retry al prossimo tick (gate-elapsed)
    assert scheduler._last_logs_archive == 0.0


def test_archive_old_logs_observability_failure_does_not_regress_archive_ok(
    monkeypatch,
):
    """B4-T4: se pg_execute observability solleva, il hook NON propaga
    e lo slot resta aggiornato.

    Best-effort semantics (ADR-0014 §B3): un fallimento della INSERT
    observability (es. DB transitorio, trigger edge case) non deve
    far regredire lo stato "archive ok" gia' committato (oggetto su
    S3, DELETE PG gia' fatta). Retry al prossimo tick archive-ok
    e' inutile: l'archive e' gia' avvenuto.
    """
    scheduler = _make_scheduler()
    now_ts = 1_000_000.0
    monkeypatch.setattr(time, "time", lambda: now_ts)

    mock_pg_execute = MagicMock(side_effect=RuntimeError("db transient"))

    with patch(
        "app.config.settings", new=_settings_ns(),
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
        return_value=MagicMock(),
    ), patch(
        "app.services.log_archive.LogArchiveWriter",
    ) as mock_writer_cls, patch(
        "app.db.pg_connection.fetchall", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.transaction", new=MagicMock(),
    ), patch(
        "app.db.pg_connection.execute", new=mock_pg_execute,
    ), patch(
        "workers.beat_scheduler.logger",
    ) as mock_logger:
        mock_writer = mock_writer_cls.return_value
        mock_writer.archive_and_purge.return_value = _writer_result(
            datetime.now(timezone.utc), archived_rows=5,
        )

        scheduler._archive_old_logs()  # must not raise

    # Slot gia' aggiornato prima della INSERT observability: resta set
    assert scheduler._last_logs_archive == now_ts, (
        "slot archive deve restare aggiornato anche se observability fallisce"
    )
    # pg_execute tentato (ha sollevato, ma senza propagare)
    assert mock_pg_execute.call_count == 1
    # logger.warning chiamato col prefisso observability (non "Errore archive")
    warning_calls = [
        c for c in mock_logger.warning.call_args_list
        if "Observability log_archive fallita" in c.args[0]
    ]
    assert len(warning_calls) == 1, (
        "deve esserci esattamente 1 warning 'Observability log_archive fallita'"
    )
