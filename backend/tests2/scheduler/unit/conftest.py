"""
Provides pytest fixtures for unit tests.

Centralizes local pytest setup for the surrounding test package.
"""

from datetime import datetime, timezone

import pytest

from app.shared.clock import FakeClock
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
def tick():
    """Forza un tick deterministico del loop heartbeat parked sul wait virtuale.

    Pattern: il thread chiama ``clock.wait(stop, interval)`` -> si parka.
    Il test aspetta che sia parked (1 waiter), poi ``advance(interval+eps)``
    libera il wait, il loop esegue ``refresh_lock``/``try_acquire`` e si
    re-parka. Estratta da `_tick` duplicato in 4 file heartbeat/lock_lost.
    """

    def _tick(fc: FakeClock, interval: float) -> None:
        fc.wait_for_pending_waiters(1)
        fc.advance(interval + 0.001)

    return _tick


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
