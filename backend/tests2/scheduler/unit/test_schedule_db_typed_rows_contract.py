from __future__ import annotations

from contextlib import contextmanager
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


def test_scheduler_db_exposes_typed_read_api_only():
    from app.core.scheduler import db as schedule_db

    assert hasattr(schedule_db, "get_schedule_row")
    assert hasattr(schedule_db, "list_schedule_rows")
    assert not hasattr(schedule_db, "get_schedule")
    assert not hasattr(schedule_db, "list_schedules")


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

    row = schedule_db.update_schedule("schedule-1", cron_expression="0 7 * * *")

    assert row is persisted
    assert isinstance(row, schedule_db.ScheduleRow)


def test_update_schedule_locked_selects_for_update_and_returns_typed_row(monkeypatch):
    from app.core.scheduler import db as schedule_db

    now = datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc)
    executed: list[tuple[str, tuple]] = []
    current = _raw_schedule_row(now)

    class FakeCursor:
        def __init__(self):
            self._last_result = None

        def execute(self, sql, params=()):
            executed.append((sql, params))
            sql_lower = sql.lower()
            if sql_lower.startswith("select * from schedules"):
                self._last_result = dict(current)
            elif sql_lower.startswith("update schedules set"):
                self._last_result = {
                    **current,
                    "config": params[0],
                    "is_enabled": params[1],
                    "updated_at": params[2],
                }

        def fetchone(self):
            result = self._last_result
            self._last_result = None
            return result

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    @contextmanager
    def fake_transaction():
        yield FakeConn()

    monkeypatch.setattr(schedule_db, "transaction", fake_transaction)

    def mutate(row):
        assert row.config == {"id": "schedule-1", "pipeline": {"project_id": "project-1"}}
        return {"config": {"id": row.id, "schedule": {"enabled": False}}, "is_enabled": False}

    row = schedule_db.update_schedule_locked("schedule-1", mutate)

    assert isinstance(row, schedule_db.ScheduleRow)
    assert row is not None
    assert row.config == {"id": "schedule-1", "schedule": {"enabled": False}}
    assert row.is_enabled is False
    assert len(executed) == 2
    assert "select * from schedules" in executed[0][0].lower()
    assert "for update" in executed[0][0].lower()
    assert executed[0][1] == ("schedule-1",)
    assert executed[1][0].lower().startswith("update schedules set")
    assert "returning *" in executed[1][0].lower()


def test_update_schedule_locked_returns_none_without_update_when_missing(monkeypatch):
    from app.core.scheduler import db as schedule_db

    executed: list[tuple[str, tuple]] = []

    class FakeCursor:
        def execute(self, sql, params=()):
            executed.append((sql, params))

        def fetchone(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    @contextmanager
    def fake_transaction():
        yield FakeConn()

    monkeypatch.setattr(schedule_db, "transaction", fake_transaction)

    def mutate(_row):
        raise AssertionError("callback must not run without a locked row")

    row = schedule_db.update_schedule_locked("missing", mutate)

    assert row is None
    assert len(executed) == 1
    assert "for update" in executed[0][0].lower()
