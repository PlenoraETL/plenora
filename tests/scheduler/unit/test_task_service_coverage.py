"""
tests/scheduler/unit/test_task_service_coverage.py
===================================================
Unit tests for TaskService, TaskInfo, TaskStatus, TaskType.

Covers:
  - TaskStatus / TaskType enum values
  - TaskInfo.to_dict() serialization
  - TaskService.create_pipeline_task
  - TaskService.create_preview_task
  - TaskService.create_enrichment_task
  - TaskService.create_carica_task
  - TaskService.get_task_status (all Celery state mappings)
  - TaskService.cancel_task (success + error)
  - TaskService.list_active_tasks (success + error)
  - TaskService.get_task_result (success + not ready)
  - TaskService.cleanup_old_results
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

pytestmark = pytest.mark.unit


# ─── Imports under test ──────────────────────────────────────────────────────

from app.services.task_service import (
    TaskService,
    TaskInfo,
    TaskStatus,
    TaskType,
)


# =============================================================================
# ENUMS
# =============================================================================


class TestTaskStatusEnum:

    def test_values(self):
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.STARTED == "started"
        assert TaskStatus.PROGRESS == "progress"
        assert TaskStatus.SUCCESS == "success"
        assert TaskStatus.FAILURE == "failure"
        assert TaskStatus.REVOKED == "revoked"
        assert TaskStatus.RETRY == "retry"

    def test_is_str_enum(self):
        assert isinstance(TaskStatus.PENDING, str)

    def test_membership(self):
        assert "pending" in [s.value for s in TaskStatus]
        assert "retry" in [s.value for s in TaskStatus]


class TestTaskTypeEnum:

    def test_values(self):
        assert TaskType.ENRICHMENT == "enrichment"
        assert TaskType.CARICA == "carica"
        assert TaskType.EXECUTE == "execute"
        assert TaskType.PREVIEW == "preview"

    def test_is_str_enum(self):
        assert isinstance(TaskType.EXECUTE, str)


# =============================================================================
# TASK INFO
# =============================================================================


class TestTaskInfo:

    def test_to_dict_basic(self):
        info = TaskInfo(
            task_id="t1",
            task_type=TaskType.EXECUTE,
            project_id="p1",
            status=TaskStatus.PENDING,
        )
        d = info.to_dict()
        assert d["task_id"] == "t1"
        assert d["task_type"] == "execute"
        assert d["status"] == "pending"
        assert d["progress"] == 0
        assert d["message"] == ""
        assert d["result"] is None
        assert d["error"] is None

    def test_to_dict_with_all_fields(self):
        info = TaskInfo(
            task_id="t2",
            task_type=TaskType.CARICA,
            project_id="p2",
            status=TaskStatus.SUCCESS,
            progress=100,
            message="Done",
            result={"rows": 42},
            error=None,
            created_at="2026-01-01T00:00:00",
            started_at="2026-01-01T00:00:01",
            completed_at="2026-01-01T00:00:10",
        )
        d = info.to_dict()
        assert d["task_type"] == "carica"
        assert d["status"] == "success"
        assert d["progress"] == 100
        assert d["result"] == {"rows": 42}
        assert d["created_at"] == "2026-01-01T00:00:00"

    def test_to_dict_enum_values_are_strings(self):
        info = TaskInfo(
            task_id="t3",
            task_type=TaskType.PREVIEW,
            project_id="p3",
            status=TaskStatus.REVOKED,
        )
        d = info.to_dict()
        # Enums should be serialized to their .value strings
        assert isinstance(d["task_type"], str)
        assert isinstance(d["status"], str)


# =============================================================================
# TASK SERVICE — CREATION METHODS
# =============================================================================


class TestCreateEnrichmentTask:

    @patch("app.services.task_service.celery_app")
    def test_create_enrichment_task(self, mock_celery):
        svc = TaskService()
        mock_result = MagicMock()
        mock_result.id = "task-enrich-001"

        with patch(
            "workers.tasks.enrichment_task.execute_enrichment_task"
        ) as mock_task:
            mock_task.delay.return_value = mock_result
            info = svc.create_enrichment_task(
                project_id="proj-1",
                canvas={"nodes": []},
                output_format="csv",
                force_recalculate=True,
            )

        assert info.task_id == "task-enrich-001"
        assert info.task_type == TaskType.ENRICHMENT
        assert info.project_id == "proj-1"
        assert info.status == TaskStatus.PENDING
        assert info.progress == 0
        mock_task.delay.assert_called_once_with(
            project_id="proj-1",
            canvas={"nodes": []},
            output_format="csv",
            force_recalculate=True,
        )


class TestCreateCaricaTask:

    @patch("app.services.task_service.celery_app")
    def test_create_carica_task(self, mock_celery):
        svc = TaskService()
        mock_result = MagicMock()
        mock_result.id = "task-carica-001"

        with patch("workers.tasks.carica_task.execute_carica_task") as mock_task:
            mock_task.delay.return_value = mock_result
            info = svc.create_carica_task(
                project_id="proj-2",
                canvas={"nodes": [], "edges": []},
                carica_node_data={"table": "dest"},
                output_format="geojson",
                force_recalculate=False,
                triggered_by="cron",
            )

        assert info.task_id == "task-carica-001"
        assert info.task_type == TaskType.CARICA
        assert info.status == TaskStatus.PENDING
        mock_task.delay.assert_called_once_with(
            project_id="proj-2",
            canvas={"nodes": [], "edges": []},
            carica_node_data={"table": "dest"},
            output_format="geojson",
            force_recalculate=False,
            triggered_by="cron",
        )


class TestCreatePipelineTask:

    @patch("app.services.task_service.celery_app")
    def test_create_pipeline_task(self, mock_celery):
        svc = TaskService()
        mock_result = MagicMock()
        mock_result.id = "task-pipe-001"

        with patch(
            "workers.tasks.pipeline_task.execute_pipeline_task"
        ) as mock_task:
            mock_task.delay.return_value = mock_result
            info = svc.create_pipeline_task(
                project_id="proj-3",
                force_recalculate=True,
                triggered_by="manual",
            )

        assert info.task_id == "task-pipe-001"
        assert info.task_type == TaskType.EXECUTE
        assert info.status == TaskStatus.PENDING
        assert info.message == "Pipeline in coda..."
        mock_task.delay.assert_called_once_with(
            project_id="proj-3",
            force_recalculate=True,
            triggered_by="manual",
        )


class TestCreatePreviewTask:

    @patch("app.services.task_service.celery_app")
    def test_create_preview_task(self, mock_celery):
        svc = TaskService()
        mock_result = MagicMock()
        mock_result.id = "task-prev-001"

        with patch(
            "workers.tasks.pipeline_task.preview_pipeline_task"
        ) as mock_task:
            mock_task.delay.return_value = mock_result
            info = svc.create_preview_task(project_id="proj-4", limit=50)

        assert info.task_id == "task-prev-001"
        assert info.task_type == TaskType.PREVIEW
        assert info.status == TaskStatus.PENDING
        assert info.message == "Preview in coda..."
        mock_task.delay.assert_called_once_with(
            project_id="proj-4", limit=50
        )


# =============================================================================
# GET TASK STATUS — all Celery state mappings
# =============================================================================


class TestGetTaskStatus:

    @patch("app.services.task_service.celery_app")
    @patch("app.services.task_service.AsyncResult")
    def test_pending(self, mock_ar_cls, mock_celery):
        mock_ar = MagicMock()
        mock_ar.state = "PENDING"
        mock_ar.info = None
        mock_ar.name = None
        mock_ar_cls.return_value = mock_ar

        svc = TaskService()
        info = svc.get_task_status("t-1")
        assert info.status == TaskStatus.PENDING
        assert info.progress == 0

    @patch("app.services.task_service.celery_app")
    @patch("app.services.task_service.AsyncResult")
    def test_started(self, mock_ar_cls, mock_celery):
        mock_ar = MagicMock()
        mock_ar.state = "STARTED"
        mock_ar.info = {"progress": 10, "message": "Running"}
        mock_ar.name = "pipeline.execute"
        mock_ar_cls.return_value = mock_ar

        svc = TaskService()
        info = svc.get_task_status("t-2")
        assert info.status == TaskStatus.STARTED
        assert info.progress == 10
        assert info.message == "Running"

    @patch("app.services.task_service.celery_app")
    @patch("app.services.task_service.AsyncResult")
    def test_progress_with_percent_fallback(self, mock_ar_cls, mock_celery):
        mock_ar = MagicMock()
        mock_ar.state = "PROGRESS"
        mock_ar.info = {"percent": 55.0, "message": "halfway"}
        mock_ar.name = None
        mock_ar_cls.return_value = mock_ar

        svc = TaskService()
        info = svc.get_task_status("t-3")
        assert info.status == TaskStatus.PROGRESS
        assert info.progress == 55
        assert isinstance(info.progress, int)

    @patch("app.services.task_service.celery_app")
    @patch("app.services.task_service.AsyncResult")
    def test_success(self, mock_ar_cls, mock_celery):
        mock_ar = MagicMock()
        mock_ar.state = "SUCCESS"
        mock_ar.info = {"project_id": "proj-x", "rows": 100}
        mock_ar.name = "carica.execute"
        mock_ar_cls.return_value = mock_ar

        svc = TaskService()
        info = svc.get_task_status("t-4")
        assert info.status == TaskStatus.SUCCESS
        assert info.progress == 100
        assert info.message == "Completato"
        assert info.result == {"project_id": "proj-x", "rows": 100}
        assert info.project_id == "proj-x"
        assert info.task_type == TaskType.CARICA

    @patch("app.services.task_service.celery_app")
    @patch("app.services.task_service.AsyncResult")
    def test_failure_with_dict_error(self, mock_ar_cls, mock_celery):
        mock_ar = MagicMock()
        mock_ar.state = "FAILURE"
        mock_ar.info = {"error": "Connection timeout"}
        mock_ar.name = None
        mock_ar_cls.return_value = mock_ar

        svc = TaskService()
        info = svc.get_task_status("t-5")
        assert info.status == TaskStatus.FAILURE
        assert info.error == "Connection timeout"
        assert info.result is None

    @patch("app.services.task_service.celery_app")
    @patch("app.services.task_service.AsyncResult")
    def test_failure_with_exception_info(self, mock_ar_cls, mock_celery):
        """When info is a non-dict (exception), fallback to str()."""
        mock_ar = MagicMock()
        mock_ar.state = "FAILURE"
        mock_ar.info = "some string error"
        mock_ar.name = None
        mock_ar_cls.return_value = mock_ar

        svc = TaskService()
        info = svc.get_task_status("t-6")
        assert info.status == TaskStatus.FAILURE
        assert info.error == "some string error"

    @patch("app.services.task_service.celery_app")
    @patch("app.services.task_service.AsyncResult")
    def test_revoked(self, mock_ar_cls, mock_celery):
        mock_ar = MagicMock()
        mock_ar.state = "REVOKED"
        mock_ar.info = {}
        mock_ar.name = None
        mock_ar_cls.return_value = mock_ar

        svc = TaskService()
        info = svc.get_task_status("t-7")
        assert info.status == TaskStatus.REVOKED

    @patch("app.services.task_service.celery_app")
    @patch("app.services.task_service.AsyncResult")
    def test_retry(self, mock_ar_cls, mock_celery):
        mock_ar = MagicMock()
        mock_ar.state = "RETRY"
        mock_ar.info = {"message": "retrying..."}
        mock_ar.name = None
        mock_ar_cls.return_value = mock_ar

        svc = TaskService()
        info = svc.get_task_status("t-8")
        assert info.status == TaskStatus.RETRY

    @patch("app.services.task_service.celery_app")
    @patch("app.services.task_service.AsyncResult")
    def test_unknown_state_defaults_to_pending(self, mock_ar_cls, mock_celery):
        mock_ar = MagicMock()
        mock_ar.state = "SOME_UNKNOWN_STATE"
        mock_ar.info = {}
        mock_ar.name = None
        mock_ar_cls.return_value = mock_ar

        svc = TaskService()
        info = svc.get_task_status("t-9")
        assert info.status == TaskStatus.PENDING

    @patch("app.services.task_service.celery_app")
    @patch("app.services.task_service.AsyncResult")
    def test_task_type_detection_enrichment(self, mock_ar_cls, mock_celery):
        mock_ar = MagicMock()
        mock_ar.state = "STARTED"
        mock_ar.info = {}
        mock_ar.name = "tasks.execute_enrichment"
        mock_ar_cls.return_value = mock_ar

        svc = TaskService()
        info = svc.get_task_status("t-10")
        assert info.task_type == TaskType.ENRICHMENT

    @patch("app.services.task_service.celery_app")
    @patch("app.services.task_service.AsyncResult")
    def test_task_type_detection_preview(self, mock_ar_cls, mock_celery):
        mock_ar = MagicMock()
        mock_ar.state = "STARTED"
        mock_ar.info = {}
        mock_ar.name = "pipeline.preview"
        mock_ar_cls.return_value = mock_ar

        svc = TaskService()
        info = svc.get_task_status("t-11")
        assert info.task_type == TaskType.PREVIEW

    @patch("app.services.task_service.celery_app")
    @patch("app.services.task_service.AsyncResult")
    def test_task_type_detection_pipeline(self, mock_ar_cls, mock_celery):
        mock_ar = MagicMock()
        mock_ar.state = "STARTED"
        mock_ar.info = {}
        mock_ar.name = "pipeline.execute"
        mock_ar_cls.return_value = mock_ar

        svc = TaskService()
        info = svc.get_task_status("t-12")
        assert info.task_type == TaskType.EXECUTE

    @patch("app.services.task_service.celery_app")
    @patch("app.services.task_service.AsyncResult")
    def test_failure_with_error_message_key(self, mock_ar_cls, mock_celery):
        """Fallback to 'error_message' key when 'error' is absent."""
        mock_ar = MagicMock()
        mock_ar.state = "FAILURE"
        mock_ar.info = {"error_message": "something broke"}
        mock_ar.name = None
        mock_ar_cls.return_value = mock_ar

        svc = TaskService()
        info = svc.get_task_status("t-13")
        assert info.status == TaskStatus.FAILURE
        assert info.error == "something broke"


# =============================================================================
# CANCEL TASK
# =============================================================================


class TestCancelTask:

    @patch("app.services.task_service.celery_app")
    def test_cancel_success(self, mock_celery):
        svc = TaskService()
        result = svc.cancel_task("task-to-cancel")
        assert result is True
        svc.app.control.revoke.assert_called_once_with(
            "task-to-cancel", terminate=True
        )

    @patch("app.services.task_service.celery_app")
    def test_cancel_error_returns_false(self, mock_celery):
        svc = TaskService()
        svc.app.control.revoke.side_effect = Exception("Redis down")
        result = svc.cancel_task("task-fail")
        assert result is False


# =============================================================================
# LIST ACTIVE TASKS
# =============================================================================


class TestListActiveTasks:

    @patch("app.services.task_service.celery_app")
    def test_list_active_with_tasks(self, mock_celery):
        svc = TaskService()
        mock_inspect = MagicMock()
        mock_inspect.active.return_value = {
            "worker1": [
                {"id": "t-a1", "name": "pipeline.execute"},
            ]
        }
        mock_inspect.reserved.return_value = {
            "worker1": [
                {"id": "t-r1", "name": "carica.execute"},
            ]
        }
        svc.app.control.inspect.return_value = mock_inspect

        tasks = svc.list_active_tasks()
        assert len(tasks) == 2
        assert tasks[0]["task_id"] == "t-a1"
        assert tasks[0]["status"] == "active"
        assert tasks[1]["task_id"] == "t-r1"
        assert tasks[1]["status"] == "reserved"

    @patch("app.services.task_service.celery_app")
    def test_list_active_empty(self, mock_celery):
        svc = TaskService()
        mock_inspect = MagicMock()
        mock_inspect.active.return_value = {}
        mock_inspect.reserved.return_value = {}
        svc.app.control.inspect.return_value = mock_inspect

        tasks = svc.list_active_tasks()
        assert tasks == []

    @patch("app.services.task_service.celery_app")
    def test_list_active_none_responses(self, mock_celery):
        """When inspect returns None (no workers), should not crash."""
        svc = TaskService()
        mock_inspect = MagicMock()
        mock_inspect.active.return_value = None
        mock_inspect.reserved.return_value = None
        svc.app.control.inspect.return_value = mock_inspect

        tasks = svc.list_active_tasks()
        assert tasks == []

    @patch("app.services.task_service.celery_app")
    def test_list_active_exception_returns_empty(self, mock_celery):
        svc = TaskService()
        svc.app.control.inspect.side_effect = Exception("Connection refused")

        tasks = svc.list_active_tasks()
        assert tasks == []


# =============================================================================
# GET TASK RESULT
# =============================================================================


class TestGetTaskResult:

    @patch("app.services.task_service.celery_app")
    @patch("app.services.task_service.AsyncResult")
    def test_get_result_success(self, mock_ar_cls, mock_celery):
        mock_ar = MagicMock()
        mock_ar.state = "SUCCESS"
        mock_ar.result = {"output": "data.csv", "rows": 500}
        mock_ar_cls.return_value = mock_ar

        svc = TaskService()
        result = svc.get_task_result("t-done")
        assert result == {"output": "data.csv", "rows": 500}

    @patch("app.services.task_service.celery_app")
    @patch("app.services.task_service.AsyncResult")
    def test_get_result_not_ready(self, mock_ar_cls, mock_celery):
        mock_ar = MagicMock()
        mock_ar.state = "STARTED"
        mock_ar_cls.return_value = mock_ar

        svc = TaskService()
        result = svc.get_task_result("t-running")
        assert result is None

    @patch("app.services.task_service.celery_app")
    @patch("app.services.task_service.AsyncResult")
    def test_get_result_failed(self, mock_ar_cls, mock_celery):
        mock_ar = MagicMock()
        mock_ar.state = "FAILURE"
        mock_ar_cls.return_value = mock_ar

        svc = TaskService()
        result = svc.get_task_result("t-failed")
        assert result is None


# =============================================================================
# CLEANUP OLD RESULTS
# =============================================================================


class TestCleanupOldResults:

    @patch("app.services.task_service.celery_app")
    def test_cleanup_returns_zero(self, mock_celery):
        svc = TaskService()
        count = svc.cleanup_old_results(max_age_hours=48)
        assert count == 0

    @patch("app.services.task_service.celery_app")
    def test_cleanup_default_hours(self, mock_celery):
        svc = TaskService()
        count = svc.cleanup_old_results()
        assert count == 0
