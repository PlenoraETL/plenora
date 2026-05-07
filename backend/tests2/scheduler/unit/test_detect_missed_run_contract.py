"""Funzione pura `detect_missed_run` — rileva se uno schedule ha
un run mancante da recuperare (C3 fix 3: missed runs catchup).

Contratto
---------
`detect_missed_run(cron_expression, last_run, now, grace_hours)
-> Optional[datetime]`:
- Ritorna `None` se NON c'e' un missed run da recuperare:
  - last_run is None (primo run di sempre)
  - last_run >= expected_last (nessun tick perso)
  - expected_last troppo vecchio (oltre grace_hours)
  - cron expression invalida
- Ritorna il timestamp dell'`expected_last` cron tick se c'e' un
  missed run da recuperare (entro la grace window).

"expected_last" = l'ultimo tick cron calcolato a partire da `now`.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.scheduler.schedule_service import detect_missed_run


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


# No-catchup cases (ritorna None)


def test_returns_none_when_last_run_is_none():
    """Primo run di sempre: niente da recuperare."""
    now = datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc)
    result = detect_missed_run("0 6 * * *", last_run=None, now=now)
    assert result is None


def test_returns_none_when_last_run_equals_expected_last():
    """Il run precedente e' esattamente l'expected_last: ok, no missed."""
    now = datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc)
    # Con cron "0 6 * * *" e now=10:00, expected_last e' oggi 06:00
    last_run = datetime(2026, 4, 16, 6, 0, tzinfo=timezone.utc)
    result = detect_missed_run("0 6 * * *", last_run=last_run, now=now)
    assert result is None


def test_returns_none_when_last_run_more_recent_than_expected():
    """last_run > expected_last (es. manuale): no missed."""
    now = datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc)
    # last_run alle 8:00 di oggi > expected_last (6:00 di oggi)
    last_run = datetime(2026, 4, 16, 8, 0, tzinfo=timezone.utc)
    result = detect_missed_run("0 6 * * *", last_run=last_run, now=now)
    assert result is None


# Catchup cases (ritorna datetime)


def test_detects_missed_run_within_grace():
    """last_run vecchio di 1 giorno, ieri 06:00. Oggi alle 10:00 abbiamo
    missed il tick di oggi 06:00. Grace default 24h → recuperabile."""
    now = datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc)
    # Ultimo run: ieri alle 06:00
    last_run = datetime(2026, 4, 15, 6, 0, tzinfo=timezone.utc)

    result = detect_missed_run("0 6 * * *", last_run=last_run, now=now)

    # expected_last = oggi 06:00
    assert result == datetime(2026, 4, 16, 6, 0, tzinfo=timezone.utc)


def test_detects_hourly_missed_run():
    """Schedule ogni ora, last_run 2h fa: il tick dell'ultima ora
    (oggi HH:00 con HH = now.hour) e' missed."""
    now = datetime(2026, 4, 16, 12, 30, tzinfo=timezone.utc)
    last_run = datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc)

    result = detect_missed_run("0 * * * *", last_run=last_run, now=now)

    # Cron "ogni ora" → expected_last = 12:00
    assert result == datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)


# Grace window enforcement


def test_returns_none_when_missed_beyond_grace_hours():
    """expected_last piu' vecchio di grace_hours: troppo vecchio, skip."""
    now = datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc)
    # Last run 3 giorni fa
    last_run = datetime(2026, 4, 13, 6, 0, tzinfo=timezone.utc)

    # grace=24h: expected_last (oggi 6:00) dista 4h da now → RECUPERABILE
    result = detect_missed_run("0 6 * * *", last_run=last_run, now=now, grace_hours=24)
    assert result == datetime(2026, 4, 16, 6, 0, tzinfo=timezone.utc)

    # grace=2h: expected_last (oggi 6:00) dista 4h da now → TROPPO VECCHIO
    result = detect_missed_run("0 6 * * *", last_run=last_run, now=now, grace_hours=2)
    assert result is None


def test_respects_custom_grace_hours_tight_window():
    """Grace=1h permette solo missed entro 60 min."""
    now = datetime(2026, 4, 16, 12, 30, tzinfo=timezone.utc)
    # Missed entro 30 min → ok
    last_run = datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc)
    result = detect_missed_run("0 * * * *", last_run=last_run, now=now, grace_hours=1)
    assert result == datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)

    # Ora sposta `now` avanti nel tempo: tick missed > 1h, oltre grace
    now_late = datetime(2026, 4, 16, 14, 30, tzinfo=timezone.utc)
    result = detect_missed_run("0 * * * *", last_run=last_run, now=now_late, grace_hours=1)
    # expected_last = 14:00, distanza da now_late (14:30) = 30 min → OK
    assert result == datetime(2026, 4, 16, 14, 0, tzinfo=timezone.utc)


# Error handling: graceful None


def test_returns_none_on_invalid_cron_expression():
    now = datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc)
    last_run = datetime(2026, 4, 15, 6, 0, tzinfo=timezone.utc)

    result = detect_missed_run("not a cron", last_run=last_run, now=now)

    assert result is None


# Timezone handling


def test_handles_timezone_aware_datetimes():
    """I datetime passati devono essere tz-aware. Il confronto deve
    funzionare correttamente anche se last_run e now sono tz-aware."""
    now = datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc)
    last_run = datetime(2026, 4, 15, 6, 0, tzinfo=timezone.utc)

    result = detect_missed_run("0 6 * * *", last_run=last_run, now=now)

    assert result is not None
    assert result.tzinfo is not None
