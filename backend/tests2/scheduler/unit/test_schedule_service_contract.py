from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.core.scheduler.models import ScheduleUpdate
from app.core.scheduler.schedule_service import ScheduleService


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
