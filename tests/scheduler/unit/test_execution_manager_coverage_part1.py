"""
tests/scheduler/unit/test_execution_manager_coverage.py
========================================================
Additional coverage tests for ExecutionManager.

Targets uncovered lines:
  - 58-63:   __init__ — real constructor creates services
  - 67-70:   _get_connection_service — lazy singleton
  - 79-96:   _get_orchestrator — lazy import + build
  - 150-151: execute() — group_id read fails silently
  - 239-245: execute() — pre-check failure raises ExecutionError
  - 498, 504-505: _archive_output — files_to_copy empty / fallback iterdir
  - 525-527: _archive_output — exception during copy
  - 566:     _execute_carica — fallback raw path from pipeline_result
  - 581-583: _execute_carica — cleanup temp file (urby_cached_)
  - 718-719: _handle_failure — debug_payload builder exception
  - 752-755: _handle_failure — retry scheduling fails → update_runtime failed
"""

import pytest
import time
import tempfile
import os
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock

pytestmark = pytest.mark.unit

from app.core.scheduler.execution_manager import ExecutionManager, ExecutionError
from app.core.scheduler.models import (
    ScheduleConfig, ScheduleFrequency, SchedulePipeline,
    ScheduleRuntime, ScheduleExecution, ScheduleErrorPolicy,
    ScheduleConflictPolicy, ScheduleConcurrency, SchedulePreCheck,
    ScheduleDestination, LogStep,
)


# -- Helpers ------------------------------------------------------------------

def _make_config(
    schedule_id="sched-001",
    project_id="proj-1",
    with_destination=False,
    verify_connection=False,
    max_retries=3,
    use_cache=False,
):
    now = datetime.now(timezone.utc)
    dest = None
    if with_destination:
        dest = ScheduleDestination(
            connection_id="conn-1",
            table_name="test_table",
            write_mode="append",
        )
    return ScheduleConfig(
        id=schedule_id,
        created_at=now,
        updated_at=now,
        pipeline=SchedulePipeline(project_id=project_id, project_name="Test", project_type="estrai"),
        schedule=ScheduleFrequency(cron_expression="0 6 * * 1"),
        execution=ScheduleExecution(use_cache=use_cache),
        on_error=ScheduleErrorPolicy(max_retries=max_retries, retry_backoff_seconds=[60, 300, 900]),
        conflict_policy=ScheduleConflictPolicy(),
        concurrency=ScheduleConcurrency(max_queue_size=3),
        pre_check=SchedulePreCheck(verify_destination_connection=verify_connection),
        runtime=ScheduleRuntime(),
        destination=dest,
    )


def _make_manager():
    """ExecutionManager built with __new__ and mocked services."""
    mgr = ExecutionManager.__new__(ExecutionManager)
    mgr.schedule_service = MagicMock()
    mgr.log_service = MagicMock()
    mgr.concurrency = MagicMock()
    mgr.policy = MagicMock()
    mgr._connection_service = None
    mgr.log_service.create_log_entry.return_value = MagicMock()
    mgr.log_service.write_log.return_value = "2026/sched_001.json"
    mgr.concurrency.try_acquire.return_value = "acquired"
    mgr.concurrency.release.return_value = True
    mgr.policy.decide_after_success.return_value = {
        "status": "completed", "event": "PIPELINE_COMPLETED", "log_level": "INFO"
    }
    mgr.policy.decide_after_failure.return_value = {
        "action": "give_up", "wait_seconds": 0,
        "attempt": 0, "event": "RETRY_EXHAUSTED", "log_level": "ERROR"
    }
    return mgr


def _make_project_meta(group_id="default"):
    meta = MagicMock()
    meta.group_id = group_id
    return meta




# =============================================================================
# Lines 58-63: __init__ — real constructor
# =============================================================================

class TestInit:

    def test_init_creates_services(self):
        """Real __init__ instantiates ScheduleService, LogService, ConcurrencyManager, PolicyEngine."""
        with patch("app.core.scheduler.execution_manager.ScheduleService") as mock_ss, \
             patch("app.core.scheduler.execution_manager.ScheduleLogService") as mock_ls, \
             patch("app.core.scheduler.execution_manager.ConcurrencyManager") as mock_cm, \
             patch("app.core.scheduler.execution_manager.PolicyEngine") as mock_pe:
            mgr = ExecutionManager()

        mock_ss.assert_called_once()
        mock_ls.assert_called_once()
        mock_cm.assert_called_once()
        mock_pe.assert_called_once()
        assert mgr._connection_service is None


# =============================================================================
# Lines 67-70: _get_connection_service — lazy singleton
# =============================================================================

class TestGetConnectionService:

    def test_lazy_creates_once(self):
        mgr = _make_manager()
        assert mgr._connection_service is None

        with patch("app.services.connection_service.ConnectionService") as mock_cls:
            svc1 = mgr._get_connection_service()
            svc2 = mgr._get_connection_service()

        # Created exactly once
        mock_cls.assert_called_once()
        assert svc1 is svc2
        assert mgr._connection_service is svc1

    def test_returns_existing_if_set(self):
        mgr = _make_manager()
        sentinel = MagicMock()
        mgr._connection_service = sentinel

        result = mgr._get_connection_service()
        assert result is sentinel


# =============================================================================
# Lines 79-96: _get_orchestrator — lazy import + creation
# =============================================================================

class TestGetOrchestrator:

    def test_creates_orchestrator_service(self):
        mgr = _make_manager()
        mgr._connection_service = MagicMock()  # avoid extra lazy import

        with patch("app.services.project_service.ProjectService") as mock_ps, \
             patch("app.db.query_executor.QueryExecutor") as mock_qe, \
             patch("app.core.estrai.query_generator.QueryGenerator") as mock_qg, \
             patch("app.core.orchestrator.service.OrchestratorService") as mock_orch, \
             patch("app.core.features.features_service.FeaturesService") as mock_fs, \
             patch("app.utils.dataframe.save_dataframe") as mock_save, \
             patch("app.core.scheduler.execution_manager.settings") as mock_settings:
            mock_settings.RESULTS_DIR = Path("/tmp/results")
            result = mgr._get_orchestrator()

        mock_ps.assert_called_once()
        mock_qg.assert_called_once()
        mock_qe.assert_called_once()
        mock_fs.assert_called_once()
        mock_orch.assert_called_once()
        assert result is mock_orch.return_value


# =============================================================================
# Lines 150-151: execute() — group_id read exception caught silently
# =============================================================================

class TestExecuteGroupIdFallback:

    def test_group_id_read_fails_silently(self):
        """If reading project group_id throws, execute continues with empty string."""
        mgr = _make_manager()
        config = _make_config()
        mgr.schedule_service.get_schedule.return_value = config

        with patch("app.services.project_service.ProjectService") as mock_ps, \
             patch("app.db.db_service.get_schedule", return_value={"group_id": "default"}):
            # First call (line 148, inside try) raises; second call (line 171) returns meta
            call_count = [0]
            real_cls = mock_ps.return_value

            def side_effect_get_meta(pid):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise RuntimeError("boom")
                return _make_project_meta()

            real_cls.get_project_metadata.side_effect = side_effect_get_meta

            with patch.object(mgr, "_get_orchestrator") as mock_orch, \
                 patch.object(mgr, "_archive_output", return_value=None):
                mock_orch.return_value.execute_pipeline.return_value = {
                    "success": True, "rows": 5
                }
                result = mgr.execute("sched-001")

        assert result["success"] is True


# =============================================================================
# Lines 239-245: Pre-check connection failure raises ExecutionError
# =============================================================================

class TestPreCheckFailureRaisesExecutionError:

    def test_pre_check_fail_returns_failed(self):
        """When pre-check returns False, execute treats it as failure."""
        mgr = _make_manager()
        config = _make_config(with_destination=True, verify_connection=True)
        mgr.schedule_service.get_schedule.return_value = config

        with patch("app.services.project_service.ProjectService") as mock_ps, \
             patch("app.db.db_service.get_schedule", return_value={"group_id": "default"}), \
             patch.object(mgr, "_pre_check_connection", return_value=False):
            mock_ps.return_value.get_project_metadata.return_value = _make_project_meta()
            result = mgr.execute("sched-001")

        assert result["success"] is False

    def test_pre_check_success_adds_step(self):
        """When pre-check succeeds, a CARICA step is appended and pipeline runs."""
        mgr = _make_manager()
        config = _make_config(with_destination=True, verify_connection=True)
        mgr.schedule_service.get_schedule.return_value = config

        with patch("app.services.project_service.ProjectService") as mock_ps, \
             patch("app.db.db_service.get_schedule", return_value={"group_id": "default"}), \
             patch.object(mgr, "_pre_check_connection", return_value=True), \
             patch.object(mgr, "_get_orchestrator") as mock_orch, \
             patch.object(mgr, "_archive_output", return_value=None), \
             patch.object(mgr, "_execute_carica") as mock_carica:
            mock_ps.return_value.get_project_metadata.return_value = _make_project_meta()
            mock_orch.return_value.execute_pipeline.return_value = {
                "success": True, "rows": 10
            }
            mock_carica.return_value = {"success": True, "rows_written": 10, "rows_skipped": 0}
            result = mgr.execute("sched-001")

        assert result["success"] is True


# =============================================================================
# Lines 498, 504-505: _archive_output — files_to_copy empty via iterdir fallback
# =============================================================================

class TestArchiveOutputEmptyFiles:

    def test_iterdir_no_files_returns_none(self):
        """output_file not specified, directory has no files -> returns None."""
        mgr = _make_manager()
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "proj-1"
            project_dir.mkdir()
            # Directory exists but has no files (only a subdirectory)
            (project_dir / "subdir").mkdir()

            with patch("app.core.scheduler.execution_manager.settings") as mock_s:
                mock_s.RESULTS_DIR = Path(tmpdir)
                result = mgr._archive_output("proj-1", "sched-001", None)

        assert result is None

    def test_iterdir_fallback_copies_all_root_files(self):
        """output_file not found by name -> falls back to iterdir, copies root files."""
        mgr = _make_manager()
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "proj-1"
            project_dir.mkdir()
            (project_dir / "data.csv").write_text("a,b\n1,2")
            (project_dir / "data2.csv").write_text("c,d\n3,4")

            with patch("app.core.scheduler.execution_manager.settings") as mock_s, \
                 patch("app.config.settings") as mock_cfg:
                mock_s.RESULTS_DIR = Path(tmpdir)
                mock_cfg.RESULTS_DIR = Path(tmpdir)
                result = mgr._archive_output("proj-1", "sched-001", "nonexistent.csv")

        assert result is not None
        assert "runs" in result


# =============================================================================
# Lines 525-527: _archive_output — exception during archive -> warning, None
# =============================================================================

class TestArchiveOutputException:

    def test_copy_exception_returns_none(self):
        """shutil.copy2 fails -> caught, returns None."""
        mgr = _make_manager()
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "proj-1"
            project_dir.mkdir()
            (project_dir / "out.csv").write_text("x")

            with patch("app.config.settings") as mock_cfg, \
                 patch("shutil.copy2", side_effect=OSError("disk full")):
                mock_cfg.RESULTS_DIR = Path(tmpdir)
                result = mgr._archive_output("proj-1", "sched-001", "out.csv")

        assert result is None


# =============================================================================
# Line 566: _execute_carica — fallback raw path from pipeline_result
# =============================================================================

class TestExecuteCaricaFallbackPath:

    def test_fallback_raw_path_used(self):
        """When result_store returns None, raw path from pipeline_result is used."""
        mgr = _make_manager()
        config = _make_config(with_destination=True)

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            f.write("a,b\n1,2")
            temp_path = f.name

        try:
            with patch("app.utils.result_store.get_result_as_tempfile", return_value=None), \
                 patch("app.utils.dataframe.load_dataframe_from_file") as mock_load, \
                 patch("app.core.carica.executor.CaricaExecutor") as mock_carica_cls, \
                 patch("app.core.carica.models.WriteStatus") as mock_ws:
                mock_df = MagicMock()
                mock_df.empty = False
                mock_df.__len__ = lambda self: 5
                mock_load.return_value = mock_df

                mock_write_result = MagicMock()
                mock_write_result.status = mock_ws.SUCCESS
                mock_write_result.rows_written = 5
                mock_write_result.rows_inserted = 5
                mock_write_result.rows_updated = 0
                mock_write_result.total_rows = 5
                mock_write_result.error_message = None
                mock_carica_cls.return_value.execute.return_value = mock_write_result

                # Ensure status check passes
                mock_ws.SUCCESS = mock_write_result.status
                mock_ws.PARTIAL = MagicMock()

                result = mgr._execute_carica(config, {"output_path": temp_path})

            assert result["success"] is True
            # Verify load was called with the fallback path
            load_call_path = mock_load.call_args[0][0]
            assert str(load_call_path) == temp_path
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


# =============================================================================
# Lines 581-583: _execute_carica — cleanup urby_cached_ temp file
# =============================================================================

class TestExecuteCaricaCacheCleanup:

    def test_urby_cached_file_deleted(self):
        """Temp file with 'urby_cached_' in name is cleaned up after load."""
        mgr = _make_manager()
        config = _make_config(with_destination=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            cached_file = Path(tmpdir) / "urby_cached_proj1.parquet"
            cached_file.write_text("fake")

            with patch("app.utils.result_store.get_result_as_tempfile",
                       return_value=cached_file), \
                 patch("app.utils.dataframe.load_dataframe_from_file") as mock_load, \
                 patch("app.core.carica.executor.CaricaExecutor") as mock_carica_cls, \
                 patch("app.core.carica.models.WriteStatus") as mock_ws:
                mock_df = MagicMock()
                mock_df.empty = False
                mock_load.return_value = mock_df

                mock_wr = MagicMock()
                mock_wr.status = mock_ws.SUCCESS
                mock_wr.rows_written = 3
                mock_wr.rows_inserted = 3
                mock_wr.rows_updated = 0
                mock_wr.total_rows = 3
                mock_wr.error_message = None
                mock_carica_cls.return_value.execute.return_value = mock_wr
                mock_ws.PARTIAL = MagicMock()

                result = mgr._execute_carica(config, {"success": True})

            assert result["success"] is True
            # The cached file should have been deleted
            assert not cached_file.exists()

    def test_urby_cached_cleanup_exception_ignored(self):
        """If os.unlink fails for cached file, exception is silently caught."""
        mgr = _make_manager()
        config = _make_config(with_destination=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            cached_file = Path(tmpdir) / "urby_cached_proj1.parquet"
            cached_file.write_text("fake")

            with patch("app.utils.result_store.get_result_as_tempfile",
                       return_value=cached_file), \
                 patch("app.utils.dataframe.load_dataframe_from_file") as mock_load, \
                 patch("app.core.carica.executor.CaricaExecutor") as mock_carica_cls, \
                 patch("app.core.carica.models.WriteStatus") as mock_ws, \
                 patch("os.unlink", side_effect=PermissionError("locked")):
                mock_df = MagicMock()
                mock_df.empty = False
                mock_load.return_value = mock_df

                mock_wr = MagicMock()
                mock_wr.status = mock_ws.SUCCESS
                mock_wr.rows_written = 3
                mock_wr.rows_inserted = 3
                mock_wr.rows_updated = 0
                mock_wr.total_rows = 3
                mock_wr.error_message = None
                mock_carica_cls.return_value.execute.return_value = mock_wr
                mock_ws.PARTIAL = MagicMock()

                # Should NOT raise despite os.unlink failure
                result = mgr._execute_carica(config, {"success": True})

            assert result["success"] is True


# =============================================================================
# Lines 718-719: _handle_failure — debug_payload builder exception -> None
# =============================================================================

class TestHandleFailureDebugPayloadException:

    def test_debug_payload_exception_sets_none(self):
        """If DebugPayloadBuilder.build raises, _debug_payload becomes None."""
        mgr = _make_manager()
        config = _make_config()
        mgr.policy.decide_after_failure.return_value = {
            "action": "give_up", "wait_seconds": 0,
            "attempt": 0, "event": "RETRY_EXHAUSTED", "log_level": "ERROR"
        }

        with patch("app.utils.debug_payload_builder.DebugPayloadBuilder") as mock_dpb:
            mock_dpb.return_value.build.side_effect = RuntimeError("build failed")

            result = mgr._handle_failure(
                config=config,
                schedule_id="sched-001",
                triggered_by="cron",
                retry_attempt=0,
                error_message="timeout",
                steps=[],
                start_time=time.time(),
                run_id="run-abc",
            )

        assert result["success"] is False
        assert result["status"] == "failed"
