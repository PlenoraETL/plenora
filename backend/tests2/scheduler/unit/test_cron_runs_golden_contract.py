"""Le «prossime esecuzioni» del wizard sono quelle del beat (UI conferma C-K2).

Il wizard degli schedule mostra le prossime esecuzioni calcolate da
``frontend/src/utils/cron.nextCronRuns`` nel fuso del beat (``BEAT_TIMEZONE``,
esposto da GET /schedules/healthcheck). Il frontend le confronta con la
fotografia ``frontend/tests/fixtures/cronRunsGolden.json``; questo test
verifica che la fotografia sia ancora quella della regola del beat
(``CronSchedule.next_runs``, croniter): se cambiano croniter, la regola o i
casi, il test fallisce e la fotografia si rigenera con
``tests2/scheduler/generate_cron_runs_golden.py``.

Verifica anche che il fuso esposto sia quello con cui il beat calcola.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.core.scheduler import cron_schedule

_ROOT = Path(__file__).resolve().parents[4]
_GOLDEN = _ROOT / "frontend" / "tests" / "fixtures" / "cronRunsGolden.json"
_GENERATOR = Path(__file__).resolve().parents[1] / "generate_cron_runs_golden.py"

pytestmark = pytest.mark.skipif(not _GOLDEN.exists(), reason="frontend non presente in questo ambiente")


def _generator():
    spec = importlib.util.spec_from_file_location("generate_cron_runs_golden", _GENERATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_golden_is_still_what_the_beat_computes() -> None:
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    current = _generator().build()
    assert golden["runs"].keys() == current["runs"].keys()
    for expr, zones in current["runs"].items():
        assert golden["runs"][expr] == zones, f"fotografia superata per {expr!r}: rigenerarla"


def test_is_due_uses_the_same_next_runs(monkeypatch) -> None:
    monkeypatch.setattr(cron_schedule, "beat_timezone", lambda: ZoneInfo("America/New_York"))
    schedule = cron_schedule.CronSchedule("0 3 * * *")
    last = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    first = schedule.next_runs(last)[0]
    assert first.strftime("%Y-%m-%d %H:%M %Z") == "2026-11-07 03:00 EST"
    assert schedule.is_due(last, first)[0] is True
    assert schedule.is_due(last, first.replace(minute=0, hour=2))[0] is False


def test_healthcheck_exposes_the_beat_timezone(monkeypatch) -> None:
    from app.core.scheduler import healthcheck_service

    monkeypatch.setattr(cron_schedule, "beat_timezone", lambda: ZoneInfo("Asia/Tokyo"))
    monkeypatch.setattr(healthcheck_service.scheduler_db, "get_beat_heartbeat", lambda: None)
    assert healthcheck_service.get_beat_healthcheck().timezone == "Asia/Tokyo"

    def boom():
        raise RuntimeError("db giu'")

    monkeypatch.setattr(healthcheck_service.scheduler_db, "get_beat_heartbeat", boom)
    assert healthcheck_service.get_beat_healthcheck().timezone == "Asia/Tokyo"
