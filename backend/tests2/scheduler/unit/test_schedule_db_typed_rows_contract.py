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
    from app.core.scheduler import db as schedule_db

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
    from app.core.scheduler import db as schedule_db

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
    from app.core.scheduler import db as schedule_db

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


def test_schedule_row_does_not_own_legacy_dict_serialization():
    from app.core.scheduler import db as schedule_db

    assert not hasattr(schedule_db.ScheduleRow, "to_legacy_dict")


def test_save_schedule_returns_typed_schedule_row(monkeypatch):
    from app.core.scheduler import db as schedule_db

    persisted = schedule_db.ScheduleRow(
        id="schedule-1",
        group_id="grp-1",
        project_id="project-1",
        name="Morning load",
        cron_expression="0 6 * * *",
        is_enabled=True,
        config={"id": "schedule-1"},
        created_at=None,
        updated_at=None,
        created_by="user-1",
    )
    monkeypatch.setattr(schedule_db, "execute", lambda *args, **kwargs: None)
    monkeypatch.setattr(schedule_db, "get_schedule_row", lambda schedule_id: persisted)
    monkeypatch.setattr(
        schedule_db,
        "get_schedule",
        lambda schedule_id: pytest.fail("save_schedule should return ScheduleRow directly"),
    )

    row = schedule_db.save_schedule(
        schedule_id="schedule-1",
        group_id="grp-1",
        project_id="project-1",
        name="Morning load",
        cron_expression="0 6 * * *",
        config={"id": "schedule-1"},
        created_by="user-1",
    )

    assert row is persisted
    assert isinstance(row, schedule_db.ScheduleRow)


def test_update_schedule_returns_typed_schedule_row(monkeypatch):
    from app.core.scheduler import db as schedule_db

    persisted = schedule_db.ScheduleRow(
        id="schedule-1",
        group_id="grp-1",
        project_id="project-1",
        name="Morning load",
        cron_expression="0 7 * * *",
        is_enabled=True,
        config={"id": "schedule-1"},
        created_at=None,
        updated_at=None,
        created_by="user-1",
    )
    monkeypatch.setattr(
        schedule_db,
        "safe_set_clause",
        lambda table, fields: ("cron_expression = %s", ("0 7 * * *",)),
    )
    monkeypatch.setattr(schedule_db, "execute", lambda *args, **kwargs: None)
    monkeypatch.setattr(schedule_db, "get_schedule_row", lambda schedule_id: persisted)
    monkeypatch.setattr(
        schedule_db,
        "get_schedule",
        lambda schedule_id: pytest.fail("update_schedule should return ScheduleRow directly"),
    )

    row = schedule_db.update_schedule("schedule-1", cron_expression="0 7 * * *")

    assert row is persisted
    assert isinstance(row, schedule_db.ScheduleRow)
