"""Scheduler-owned ownership access helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TypedDict

from app.core.scheduler import db as scheduler_db


class SchedulerOwnershipResource(TypedDict):
    id: str
    group_id: Optional[str]
    created_by: Optional[str]


@dataclass(frozen=True)
class ScheduleOwnerContext:
    schedule_id: str
    group_id: Optional[str]
    created_by: Optional[str]

    def ownership_resource(self) -> SchedulerOwnershipResource:
        return {
            "id": self.schedule_id,
            "group_id": self.group_id,
            "created_by": self.created_by,
        }


def load_schedule_owner_context(
    schedule_id: str,
) -> Optional[ScheduleOwnerContext]:
    """Load schedule ownership fields for auth owner dependencies."""
    row = scheduler_db.get_schedule_row(schedule_id)
    if row is None:
        return None
    return ScheduleOwnerContext(
        schedule_id=row.id,
        group_id=row.group_id,
        created_by=row.created_by,
    )


__all__ = [
    "ScheduleOwnerContext",
    "load_schedule_owner_context",
]
