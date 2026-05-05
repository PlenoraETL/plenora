from datetime import datetime, timezone

import pytest

from app.shared.contracts.auth import CurrentUser
from app.core.scheduler.models import (
    ScheduleConcurrency,
    ScheduleConfig,
    ScheduleConflictPolicy,
    ScheduleDestination,
    ScheduleErrorPolicy,
    ScheduleExecution,
    ScheduleFrequency,
    SchedulePipeline,
    SchedulePreCheck,
    ScheduleRuntime,
)


@pytest.fixture
def current_user() -> CurrentUser:
    return CurrentUser(
        user_id="u-1",
        username="tester",
        group_id="grp-1",
        role="admin",
    )


@pytest.fixture
def schedule_config() -> ScheduleConfig:
    now = datetime.now(timezone.utc)
    return ScheduleConfig(
        id="schedule_123456789abc",
        created_at=now,
        updated_at=now,
        pipeline=SchedulePipeline(project_id="proj-1", project_type="estrai"),
        schedule=ScheduleFrequency(cron_expression="0 6 * * *"),
        execution=ScheduleExecution(),
        on_error=ScheduleErrorPolicy(),
        conflict_policy=ScheduleConflictPolicy(),
        concurrency=ScheduleConcurrency(),
        pre_check=SchedulePreCheck(),
        runtime=ScheduleRuntime(),
        destination=ScheduleDestination(connection_id="conn-1", table_name="dest_table"),
    )
