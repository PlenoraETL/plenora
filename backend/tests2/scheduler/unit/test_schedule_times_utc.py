"""Orari degli schedule in UTC anche con la sessione PG in Europe/Rome.

Fase 5 T7, correzione dopo la conferma: ``schedule_logs.timestamp`` è un
TIMESTAMPTZ che psycopg restituisce nel fuso della SESSIONE (su Windows
nativo il fuso del sistema). Il token ``YYYYMMDD_HHMMSS`` (timestamp_str,
RunInfo.timestamp) non porta il fuso e il frontend lo legge in UTC
(utils/format.parseServerDate): va SEMPRE calcolato in UTC
(app/shared/utc_time.utc_stamp).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.core.scheduler import db as scheduler_db
from app.core.scheduler.runs_catalog import ScheduleRunsCatalog
from app.shared.utc_time import as_utc, utc_stamp

pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract, pytest.mark.regression]

ROME = ZoneInfo("Europe/Rome")
# 5 novembre 2026, 11:30 a Roma (ora solare, +01:00) = 10:30 UTC.
WINTER = datetime(2026, 11, 5, 11, 30, 0, tzinfo=ROME)
# 1 luglio 2026, 12:15 a Roma (ora legale, +02:00) = 10:15 UTC.
SUMMER = datetime(2026, 7, 1, 12, 15, 0, tzinfo=ROME)


@pytest.fixture(autouse=True)
def _no_result_rows(monkeypatch):
    monkeypatch.setattr("app.core.results.store.get_result_row", lambda _rid: None)


def test_utc_stamp_converts_session_zone_to_utc():
    assert utc_stamp(WINTER) == "20261105_103000"
    assert utc_stamp(SUMMER) == "20260701_101500"
    # Già UTC e naive (= UTC per convenzione) restano uguali.
    assert utc_stamp(datetime(2026, 11, 5, 10, 30, tzinfo=timezone.utc)) == "20261105_103000"
    assert utc_stamp(datetime(2026, 11, 5, 10, 30)) == "20261105_103000"
    assert as_utc(WINTER) == WINTER and as_utc(WINTER).tzinfo is timezone.utc


@pytest.mark.parametrize("value, expected", [(WINTER, "20261105_103000"), (SUMMER, "20260701_101500")])
def test_list_schedule_logs_timestamp_str_is_utc(monkeypatch, value, expected):
    row = {
        "log_id": "l1", "schedule_id": "schedule_x", "timestamp": value, "level": "INFO",
        "event": "PIPELINE_COMPLETED", "triggered_by": "cron", "total_duration_ms": 1,
        "error_message": None, "pipeline_info": None, "execution_info": None,
    }
    monkeypatch.setattr(scheduler_db, "fetchall", lambda _sql, _args=None: [row])
    out = scheduler_db.list_schedule_logs(schedule_id="schedule_x")
    assert out[0]["timestamp_str"] == expected


def test_run_catalog_token_from_datetime_is_utc():
    log_store = MagicMock()
    log_store.list_completed_runs.return_value = [
        {"timestamp": WINTER, "execution_info": {}},
        {"timestamp": SUMMER, "execution_info": {}},
    ]
    runs = ScheduleRunsCatalog(log_store=log_store).list_runs("proj-1", "schedule_x")
    assert [r.timestamp for r in runs] == ["20261105_103000", "20260701_101500"]
