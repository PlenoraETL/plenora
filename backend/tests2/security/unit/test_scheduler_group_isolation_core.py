"""
Core-level contract test per Refactor #2 invariante C.

Group isolation nello scheduler: uno schedule non puo' eseguire un
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.scheduler.execution_manager import ExecutionManager
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


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.regression,
]


@pytest.fixture
def schedule_config() -> ScheduleConfig:
    now = datetime.now(timezone.utc)
    return ScheduleConfig(
        id="schedule_isolation_test",
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
        destination=ScheduleDestination(connection_id="conn-1", table_name="dest"),
    )


def _make_manager(schedule_config) -> ExecutionManager:
    """ExecutionManager senza CurrentUser: lavora solo con
    schedule_id + group_id letti dal DB."""
    manager = ExecutionManager.__new__(ExecutionManager)
    manager.schedule_service = MagicMock()
    manager.log_service = MagicMock()
    manager.concurrency = MagicMock()
    manager.policy = MagicMock()
    manager._connection_service = None
    manager._get_connection_service = MagicMock(return_value=MagicMock())
    manager.schedule_service.get_schedule = MagicMock(return_value=schedule_config)
    manager.schedule_service.set_runtime_status = MagicMock()
    manager.concurrency.try_acquire = MagicMock(return_value="acquired")
    return manager


def _project_meta(group_id):
    return SimpleNamespace(id="proj-1", group_id=group_id, project_type="estrai")


# Cross-group blocked


def test_core_blocks_cross_group_execution(schedule_config):
    """schedule(grp-A) → progetto(grp-B): hard fail prima del lock."""
    manager = _make_manager(schedule_config)

    with patch("app.db.db_service.get_schedule",
               return_value={"id": "s-1", "group_id": "grp-A"}), \
         patch("app.services.project_service.ProjectService") as project_service_cls:
        project_service_cls.return_value.get_project_metadata.return_value = _project_meta("grp-B")
        result = manager.execute("s-1")

    assert result["success"] is False
    assert result["status"] == "failed"
    assert "group isolation" in result["message"].lower()
    manager.concurrency.try_acquire.assert_not_called()


# Fail-closed sui group_id mancanti (era fail-OPEN pre-Refactor #2)


def test_core_blocks_schedule_with_missing_group_id(schedule_config):
    """Schedule legacy senza group_id: pre-Refactor #2 il check con
    `if schedule_group_id and ...` saltava silente. Ora hard fail."""
    manager = _make_manager(schedule_config)

    with patch("app.db.db_service.get_schedule",
               return_value={"id": "s-1", "group_id": None}), \
         patch("app.services.project_service.ProjectService") as project_service_cls:
        project_service_cls.return_value.get_project_metadata.return_value = _project_meta("grp-B")
        result = manager.execute("s-1")

    assert result["success"] is False
    assert result["status"] == "failed"
    assert "schedule senza group_id" in result["message"]
    manager.concurrency.try_acquire.assert_not_called()


def test_core_blocks_schedule_with_empty_string_group_id(schedule_config):
    """Variante: schedule_row.group_id = '' (stringa vuota, non None).
    Anche questo deve essere fail-closed."""
    manager = _make_manager(schedule_config)

    with patch("app.db.db_service.get_schedule",
               return_value={"id": "s-1", "group_id": ""}), \
         patch("app.services.project_service.ProjectService") as project_service_cls:
        project_service_cls.return_value.get_project_metadata.return_value = _project_meta("grp-A")
        result = manager.execute("s-1")

    assert result["success"] is False
    assert result["status"] == "failed"
    assert "schedule senza group_id" in result["message"]


def test_core_blocks_project_with_missing_group_id(schedule_config):
    """Progetto senza group_id assegnato: hard fail."""
    manager = _make_manager(schedule_config)

    with patch("app.db.db_service.get_schedule",
               return_value={"id": "s-1", "group_id": "grp-A"}), \
         patch("app.services.project_service.ProjectService") as project_service_cls:
        project_service_cls.return_value.get_project_metadata.return_value = _project_meta(None)
        result = manager.execute("s-1")

    assert result["success"] is False
    assert result["status"] == "failed"
    assert "senza group_id" in result["message"]


def test_core_blocks_project_with_empty_string_group_id(schedule_config):
    manager = _make_manager(schedule_config)

    with patch("app.db.db_service.get_schedule",
               return_value={"id": "s-1", "group_id": "grp-A"}), \
         patch("app.services.project_service.ProjectService") as project_service_cls:
        project_service_cls.return_value.get_project_metadata.return_value = _project_meta("")
        result = manager.execute("s-1")

    assert result["success"] is False
    assert result["status"] == "failed"
    assert "senza group_id" in result["message"]


# Same-group passes


def test_core_allows_same_group_execution(schedule_config):
    """Sanity check: stesso group_id → isolation passa, esecuzione
    procede fino al concurrency lock."""
    manager = _make_manager(schedule_config)
    manager.concurrency.try_acquire.return_value = "queued"

    with patch("app.db.db_service.get_schedule",
               return_value={"id": "s-1", "group_id": "grp-A"}), \
         patch("app.services.project_service.ProjectService") as project_service_cls:
        project_service_cls.return_value.get_project_metadata.return_value = _project_meta("grp-A")
        result = manager.execute("s-1")

    manager.concurrency.try_acquire.assert_called_once()
    assert result["status"] == "queued"
