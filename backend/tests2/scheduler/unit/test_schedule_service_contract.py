"""
Tests the schedule service behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.core.scheduler.models import ScheduleUpdate
from app.core.scheduler.schedule_service import ScheduleService
from app.core.scheduler.db import ScheduleRow


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def _make_service() -> ScheduleService:
    return ScheduleService()


def test_update_schedule_clear_destination_disables_precheck(schedule_config):
    service = _make_service()

    with patch.object(service, "get_schedule", return_value=schedule_config), \
         patch("app.core.scheduler.schedule_service.db.update_schedule") as mock_update, \
         patch.object(service, "_calculate_next_execution", return_value=datetime.now(timezone.utc)):
        updated = service.update_schedule(
            schedule_config.id,
            ScheduleUpdate(clear_destination=True),
        )

    assert updated is not None
    assert updated.destination is None
    assert updated.pre_check.verify_destination_connection is False
    assert mock_update.called


def test_toggle_schedule_refuses_to_enable_orphan(schedule_config):
    service = _make_service()
    schedule_config.runtime.status = "orphan"

    with patch.object(service, "get_schedule", return_value=schedule_config), \
         patch("app.core.scheduler.schedule_service.db.update_schedule") as mock_update:
        result = service.toggle_schedule(schedule_config.id, enabled=True)

    assert result is None
    assert not mock_update.called


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
    monkeypatch.setattr(
        "app.core.scheduler.schedule_service.db.get_schedule",
        lambda schedule_id: pytest.fail("legacy schedule dict wrapper should not be used"),
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
    monkeypatch.setattr(
        "app.core.scheduler.schedule_service.db.list_schedules",
        lambda group_id: pytest.fail("legacy schedule dict wrapper should not be used"),
    )

    result = service.list_schedules(group_id="grp-1")

    assert result.total == 1
    assert result.schedules == [schedule_config]
