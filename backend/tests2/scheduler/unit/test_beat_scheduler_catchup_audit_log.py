"""
[phase-2-err][batch7] 7.e — beat_scheduler audit_insert catchup log elevation.

Audit Batch 7 §3.5/§2.3 [MEDIA] — `_emit_single_catchup` loggava il
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def _make_beat_scheduler():
    """BeatScheduler bypass __init__ (evita Celery Beat startup, Redis,
    schedule bootstrap) con attributi minimi necessari per chiamare
    `_emit_single_catchup`."""
    from workers.beat_scheduler import PlenoraBeatScheduler
    instance = PlenoraBeatScheduler.__new__(PlenoraBeatScheduler)
    instance.app = MagicMock()
    instance._leader_id = "leader-test"
    return instance


def _invoke_emit(instance, schedule_id="sched-1"):
    """Helper: chiamata a _emit_single_catchup con timestamps fissi."""
    expected_last = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 4, 22, 10, 5, 0, tzinfo=timezone.utc)
    return instance._emit_single_catchup(schedule_id, expected_last, now)


# HAPPY PATH — nessun error log


def test_emit_single_catchup_happy_path_no_error_log():
    """db.execute ok → nessun logger.error emesso. Tick prosegue, send_task
    chiamato una volta."""
    instance = _make_beat_scheduler()

    with patch(
        "app.db.pg_connection.execute"
    ), patch(
        "workers.beat_scheduler.logger"
    ) as mock_logger:
        _invoke_emit(instance)

    # send_task chiamato per emettere il catchup task Celery
    instance.app.send_task.assert_called_once()
    # Nessun error log emesso (happy path)
    crash_errors = [
        c for c in mock_logger.error.call_args_list
        if "[CRASH]" in str(c)
    ]
    assert crash_errors == []


# FAIL-OPEN — INSERT fail logga [CRASH] con exc_info


def test_emit_single_catchup_audit_insert_fail_logs_crash_with_exc_info():
    """db.execute solleva RuntimeError → logger.error('[CRASH] [CATCHUP]
    Audit insert fallito ...', exc_info=True) emesso. Nessuna propagazione
    (beat-safe)."""
    instance = _make_beat_scheduler()

    with patch(
        "app.db.pg_connection.execute",
        side_effect=RuntimeError("db temporarily unreachable"),
    ), patch(
        "workers.beat_scheduler.logger"
    ) as mock_logger:
        # Se propagasse, questa chiamata solleverebbe
        result = _invoke_emit(instance, schedule_id="sched-crashy")

    assert result is None  # funzione ritorna None (beat-safe)
    # Log [CRASH] [CATCHUP] emesso con exc_info=True
    crash_errors = [
        c for c in mock_logger.error.call_args_list
        if "[CRASH]" in str(c)
        and "[CATCHUP]" in str(c)
        and "Audit insert fallito" in str(c)
        and "sched-crashy" in str(c)
    ]
    assert len(crash_errors) == 1, (
        f"atteso 1 logger.error [CRASH] [CATCHUP], ricevuto: "
        f"{mock_logger.error.call_args_list}"
    )
    assert crash_errors[0].kwargs.get("exc_info") is True


# BEAT-SAFE — nessuna propagazione su transient DB


def test_emit_single_catchup_beat_safe_no_propagation():
    """db.execute solleva psycopg2.OperationalError (DB down) → funzione
    ritorna None senza raise. Beat-safe preservato: il tick continua,
    altri catchup dello stesso tick non bloccati."""
    import psycopg2
    instance = _make_beat_scheduler()

    with patch(
        "app.db.pg_connection.execute",
        side_effect=psycopg2.OperationalError("pool exhausted"),
    ), patch(
        "workers.beat_scheduler.logger"
    ):
        # Se propagasse, pytest rileverebbe l'eccezione non catturata
        result = _invoke_emit(instance, schedule_id="sched-pg-down")

    assert result is None
    # send_task comunque chiamato: l'emit Celery e' avvenuto, solo l'audit
    # history non e' stata persistita → rischio emit duplicato al prossimo tick.
    instance.app.send_task.assert_called_once()


# MESSAGGIO — menziona rischio duplicate emit


def test_emit_single_catchup_error_message_mentions_duplicate_risk():
    """Il messaggio di log CRASH deve esplicitare il rischio 'emit duplicato
    al prossimo tick' per rendere comprensibile l'anomalia all'operatore."""
    instance = _make_beat_scheduler()

    with patch(
        "app.db.pg_connection.execute",
        side_effect=RuntimeError("test"),
    ), patch(
        "workers.beat_scheduler.logger"
    ) as mock_logger:
        _invoke_emit(instance, schedule_id="sched-msg")

    crash_errors = [
        c for c in mock_logger.error.call_args_list
        if "[CRASH]" in str(c)
    ]
    assert len(crash_errors) == 1
    msg = str(crash_errors[0])
    assert "emit duplicato" in msg, (
        f"messaggio deve menzionare 'emit duplicato', ricevuto: {msg}"
    )
    assert "dedupe history incompleta" in msg, (
        f"messaggio deve menzionare 'dedupe history incompleta', ricevuto: {msg}"
    )
