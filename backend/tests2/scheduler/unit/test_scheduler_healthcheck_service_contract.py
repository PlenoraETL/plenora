"""Contract tests for scheduler Beat healthcheck use-case."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.scheduler import healthcheck_service


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def test_beat_healthcheck_returns_ok_for_recent_heartbeat(monkeypatch):
    nowish = datetime.now(timezone.utc) - timedelta(seconds=20)

    monkeypatch.setattr(
        healthcheck_service.scheduler_db,
        "get_beat_heartbeat",
        lambda: {"ts": nowish},
    )
    monkeypatch.setattr(
        healthcheck_service.scheduler_db,
        "count_enabled_schedules",
        lambda: 3,
    )
    monkeypatch.setattr(
        healthcheck_service.scheduler_db,
        "count_stuck_schedules",
        lambda hard_limit: 0,
    )

    result = healthcheck_service.get_beat_healthcheck()

    assert result.status == "ok"
    assert result.last_beat == nowish
    assert "3 schedule attivi" in result.message


def test_beat_healthcheck_warns_when_recent_heartbeat_has_stuck_tasks(monkeypatch):
    nowish = datetime.now(timezone.utc) - timedelta(seconds=20)

    monkeypatch.setattr(
        healthcheck_service.scheduler_db,
        "get_beat_heartbeat",
        lambda: {"ts": nowish},
    )
    monkeypatch.setattr(
        healthcheck_service.scheduler_db,
        "count_enabled_schedules",
        lambda: 1,
    )
    monkeypatch.setattr(
        healthcheck_service.scheduler_db,
        "count_stuck_schedules",
        lambda hard_limit: 2,
    )

    result = healthcheck_service.get_beat_healthcheck()

    assert result.status == "warning"
    assert "2 task bloccate" in result.message


def test_beat_healthcheck_returns_unknown_without_heartbeat(monkeypatch):
    monkeypatch.setattr(
        healthcheck_service.scheduler_db,
        "get_beat_heartbeat",
        lambda: None,
    )

    result = healthcheck_service.get_beat_healthcheck()

    assert result.status == "unknown"
    assert result.last_beat is None


def test_beat_healthcheck_returns_down_for_old_heartbeat(monkeypatch):
    old = datetime.now(timezone.utc) - timedelta(seconds=181)

    monkeypatch.setattr(
        healthcheck_service.scheduler_db,
        "get_beat_heartbeat",
        lambda: {"ts": old},
    )
    monkeypatch.setattr(
        healthcheck_service.scheduler_db,
        "count_enabled_schedules",
        lambda: 1,
    )

    result = healthcheck_service.get_beat_healthcheck()

    assert result.status == "down"
    assert result.last_beat == old
