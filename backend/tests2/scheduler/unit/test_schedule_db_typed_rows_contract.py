from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _raw_schedule_row(now: datetime) -> dict[str, object]:
    return {
        "id": "schedule-1",
        "group_id": "grp-1",
        "project_id": "project-1",
        "name": "Morning load",
        "cron_expression": "0 6 * * *",
        "is_enabled": True,
        "config": '{"id": "schedule-1", "pipeline": {"project_id": "project-1"}}',
        "created_at": now,
        "updated_at": now,
        "created_by": "user-1",
    }


def test_get_schedule_row_returns_immutable_schedule_row(monkeypatch):
    from app.db.config import schedules as schedule_db

    now = datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        schedule_db,
        "fetchone",
        lambda *args, **kwargs: _raw_schedule_row(now),
    )

    row = schedule_db.get_schedule_row("schedule-1")

    assert row == schedule_db.ScheduleRow(
        id="schedule-1",
        group_id="grp-1",
        project_id="project-1",
        name="Morning load",
        cron_expression="0 6 * * *",
        is_enabled=True,
        config={"id": "schedule-1", "pipeline": {"project_id": "project-1"}},
        created_at=now,
        updated_at=now,
        created_by="user-1",
    )
    with pytest.raises(FrozenInstanceError):
        row.name = "changed"


def test_list_schedule_rows_returns_typed_rows(monkeypatch):
    from app.db.config import schedules as schedule_db

    now = datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        schedule_db,
        "fetchall",
        lambda *args, **kwargs: [_raw_schedule_row(now)],
    )

    rows = schedule_db.list_schedule_rows("grp-1")

    assert rows == [
        schedule_db.ScheduleRow(
            id="schedule-1",
            group_id="grp-1",
            project_id="project-1",
            name="Morning load",
            cron_expression="0 6 * * *",
            is_enabled=True,
            config={"id": "schedule-1", "pipeline": {"project_id": "project-1"}},
            created_at=now,
            updated_at=now,
            created_by="user-1",
        )
    ]


def test_get_schedule_keeps_legacy_dict_wrapper(monkeypatch):
    from app.db.config import schedules as schedule_db

    monkeypatch.setattr(
        schedule_db,
        "get_schedule_row",
        lambda *args, **kwargs: schedule_db.ScheduleRow(
            id="schedule-1",
            group_id="grp-1",
            project_id="project-1",
            name="Morning load",
            cron_expression="0 6 * * *",
            is_enabled=True,
            config={"id": "schedule-1"},
            created_at=None,
            updated_at=None,
            created_by=None,
        ),
    )

    row = schedule_db.get_schedule("schedule-1")

    assert isinstance(row, dict)
    assert row["id"] == "schedule-1"
    assert row["config"] == {"id": "schedule-1"}
