"""
Tests the schedule service behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.scheduler.models import ScheduleUpdate
from app.core.scheduler.schedule_service import ScheduleService
from app.core.scheduler.db import ScheduleRow


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def _make_service() -> ScheduleService:
    return ScheduleService()


def _row_from_config(schedule_config: object) -> ScheduleRow:
    return ScheduleRow(
        id=schedule_config.id,
        group_id="grp-1",
        project_id=schedule_config.pipeline.project_id,
        name=schedule_config.pipeline.project_name or schedule_config.id,
        cron_expression=schedule_config.schedule.cron_expression,
        is_enabled=schedule_config.schedule.enabled,
        config=schedule_config.model_dump(mode="json"),
        created_at=schedule_config.created_at,
        updated_at=schedule_config.updated_at,
        created_by="user-1",
    )


def test_schedule_service_has_no_process_local_schedule_locks():
    source = (
        Path(__file__).resolve().parents[3]
        / "app/core/scheduler/schedule_service.py"
    ).read_text(
        encoding="utf-8"
    )

    assert "_schedule_locks" not in source
    assert "threading.RLock" not in source
    assert "threading.Lock" not in source


def test_update_schedule_clear_destination_disables_precheck(schedule_config, monkeypatch):
    service = _make_service()
    calls = []

    def fake_locked_update(schedule_id, callback):
        calls.append(schedule_id)
        fields = callback(_row_from_config(schedule_config))
        calls.append(fields)
        return _row_from_config(schedule_config) if fields is not None else None

    monkeypatch.setattr(
        "app.core.scheduler.schedule_service.db.update_schedule_locked",
        fake_locked_update,
    )

    with patch.object(service, "_calculate_next_execution", return_value=datetime.now(timezone.utc)):
        updated = service.update_schedule(
            schedule_config.id,
            ScheduleUpdate(clear_destination=True),
        )

    assert updated is not None
    assert updated.destination is None
    assert updated.pre_check.verify_destination_connection is False
    assert calls[0] == schedule_config.id
    assert calls[1] is not None
    assert "config" in calls[1]


def test_toggle_schedule_refuses_to_enable_orphan(schedule_config, monkeypatch):
    service = _make_service()
    schedule_config.runtime.status = "orphan"
    calls = []

    def fake_locked_update(schedule_id, callback):
        calls.append(schedule_id)
        fields = callback(_row_from_config(schedule_config))
        calls.append(fields)
        return _row_from_config(schedule_config) if fields is not None else None

    monkeypatch.setattr(
        "app.core.scheduler.schedule_service.db.update_schedule_locked",
        fake_locked_update,
    )

    result = service.toggle_schedule(schedule_config.id, enabled=True)

    assert result is None
    assert calls == [schedule_config.id, None]


def test_update_runtime_merges_against_locked_schedule_row(schedule_config, monkeypatch):
    service = _make_service()
    schedule_config.runtime.total_runs = 4
    schedule_config.runtime.success_count = 2
    captured = {}

    def fail_get_schedule(_schedule_id):
        raise AssertionError("runtime RMW must use the locked DB row")

    def fake_locked_update(schedule_id, callback):
        fields = callback(_row_from_config(schedule_config))
        captured["schedule_id"] = schedule_id
        captured["fields"] = fields
        return _row_from_config(schedule_config) if fields is not None else None

    monkeypatch.setattr(service, "get_schedule", fail_get_schedule)
    monkeypatch.setattr(
        "app.core.scheduler.schedule_service.db.update_schedule_locked",
        fake_locked_update,
    )
    monkeypatch.setattr(service, "_calculate_next_execution", lambda *_args: None)

    result = service.update_runtime(schedule_config.id, "completed")

    assert result is not None
    assert result.runtime.total_runs == 5
    assert result.runtime.success_count == 3
    assert captured["schedule_id"] == schedule_config.id
    assert captured["fields"]["config"]["runtime"]["total_runs"] == 5
    assert captured["fields"]["config"]["runtime"]["success_count"] == 3


def test_get_schedule_consumes_typed_schedule_row(schedule_config, monkeypatch):
    service = _make_service()

    row = ScheduleRow(
        id=schedule_config.id,
        group_id="grp-1",
        project_id=schedule_config.pipeline.project_id,
        name=schedule_config.pipeline.project_name or schedule_config.id,
        cron_expression=schedule_config.schedule.cron_expression,
        is_enabled=schedule_config.schedule.enabled,
        config=schedule_config.model_dump(mode="json"),
        created_at=schedule_config.created_at,
        updated_at=schedule_config.updated_at,
        created_by="user-1",
    )
    monkeypatch.setattr(
        "app.core.scheduler.schedule_service.db.get_schedule_row",
        lambda schedule_id: row,
    )

    result = service.get_schedule(schedule_config.id)

    assert result == schedule_config


def test_list_schedules_consumes_typed_schedule_rows(schedule_config, monkeypatch):
    service = _make_service()

    row = ScheduleRow(
        id=schedule_config.id,
        group_id="grp-1",
        project_id=schedule_config.pipeline.project_id,
        name=schedule_config.pipeline.project_name or schedule_config.id,
        cron_expression=schedule_config.schedule.cron_expression,
        is_enabled=schedule_config.schedule.enabled,
        config=schedule_config.model_dump(mode="json"),
        created_at=schedule_config.created_at,
        updated_at=schedule_config.updated_at,
        created_by="user-1",
    )
    monkeypatch.setattr(
        "app.core.scheduler.schedule_service.db.list_schedule_rows",
        lambda group_id: [row],
    )

    result = service.list_schedules(group_id="grp-1")

    assert result.total == 1
    assert result.schedules == [schedule_config]
