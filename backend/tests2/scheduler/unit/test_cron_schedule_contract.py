"""Contratto delle primitive schedule celery-free (Plenora 2.0 Fase 5 F5.4).

CronSchedule (croniter, tz Europe/Rome) + IntervalSchedule + BeatEntry,
rimpiazzo di celery crontab/schedule/ScheduleEntry.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.scheduler.cron_schedule import (
    BeatEntry,
    CronSchedule,
    IntervalSchedule,
    beat_now,
    beat_timezone,
)


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def test_beat_timezone_default_is_europe_rome():
    # Preserva l'interpretazione cron di Celery (timezone="Europe/Rome").
    assert str(beat_timezone()) == "Europe/Rome"


def test_beat_now_is_timezone_aware():
    assert beat_now().tzinfo is not None


# CronSchedule


def test_cron_invalid_raises():
    with pytest.raises(ValueError):
        CronSchedule("0 6 * *")  # 4 campi
    with pytest.raises(ValueError):
        CronSchedule("")
    with pytest.raises(ValueError):
        CronSchedule("99 6 * * *")  # minuto fuori range


def test_cron_due_when_next_fire_after_last_run_has_passed():
    sched = CronSchedule("0 6 * * *")  # ogni giorno alle 06:00 (Europe/Rome)
    tz = beat_timezone()
    last_run = datetime(2026, 6, 13, 6, 0, tzinfo=tz)
    # Adesso e' il giorno dopo alle 06:30 -> il fire delle 06:00 e' passato.
    now = datetime(2026, 6, 14, 6, 30, tzinfo=tz)
    is_due, remaining = sched.is_due(last_run, now)
    assert is_due is True
    assert remaining > 0  # prossimo fire (domani 06:00) nel futuro


def test_cron_not_due_before_next_fire():
    sched = CronSchedule("0 6 * * *")
    tz = beat_timezone()
    last_run = datetime(2026, 6, 14, 6, 0, tzinfo=tz)
    now = datetime(2026, 6, 14, 7, 0, tzinfo=tz)  # gia' passate le 06:00 di oggi
    is_due, _ = sched.is_due(last_run, now)
    assert is_due is False


def test_cron_normalizes_utc_input_to_beat_tz():
    # last_run/now in UTC: il cron deve comunque essere valutato in Europe/Rome.
    sched = CronSchedule("0 6 * * *")
    # 04:30 UTC == 06:30 Europe/Rome (estate, CEST +2): il fire delle 06:00
    # locali e' passato rispetto a un last_run del giorno prima.
    last_run = datetime(2026, 6, 13, 4, 0, tzinfo=timezone.utc)
    now = datetime(2026, 6, 14, 4, 30, tzinfo=timezone.utc)
    is_due, _ = sched.is_due(last_run, now)
    assert is_due is True


def test_cron_str_stable_for_change_detection():
    assert str(CronSchedule("0 6 * * *")) == str(CronSchedule("0  6  *  *  *"))
    assert str(CronSchedule("0 6 * * *")) != str(CronSchedule("30 6 * * *"))


# IntervalSchedule


def test_interval_due_after_elapsed():
    sched = IntervalSchedule(60.0)
    now = beat_now()
    is_due, remaining = sched.is_due(now - timedelta(seconds=61), now)
    assert is_due is True
    assert remaining == 60.0


def test_interval_not_due_before_elapsed():
    sched = IntervalSchedule(60.0)
    now = beat_now()
    is_due, remaining = sched.is_due(now - timedelta(seconds=10), now)
    assert is_due is False
    assert 0 < remaining <= 60.0


# BeatEntry


def test_beat_entry_is_due_delegates_to_schedule():
    entry = BeatEntry(
        name="e1",
        task="some.task",
        schedule=IntervalSchedule(60.0),
        kwargs={"k": "v"},
        last_run_at=beat_now() - timedelta(seconds=120),
    )
    is_due, _ = entry.is_due()
    assert is_due is True
    assert entry.task == "some.task"
    assert entry.kwargs == {"k": "v"}


def test_beat_entry_next_advances_last_run_at():
    old = beat_now() - timedelta(hours=1)
    entry = BeatEntry(
        name="e1", task="t", schedule=IntervalSchedule(60.0), last_run_at=old
    )
    advanced = next(entry)
    assert advanced.last_run_at > old
    assert advanced.task == "t"
    # L'entry originale resta immutata (next ritorna una nuova entry).
    assert entry.last_run_at == old
