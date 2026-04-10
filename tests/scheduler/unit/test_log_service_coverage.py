"""
tests/scheduler/unit/test_log_service_coverage.py
===================================================
Test mirati per coprire le linee mancanti di log_service.py:

  64        __init__ (self.logs_dir = settings.SCHEDULER_LOGS_DIR)
  86        _find_log_file returns None when logs_dir doesn't exist
  119-121   write_log except Exception branch
  135-137   get_log except Exception (corrupt JSON)
  177       get_logs status_filter filtering
  181-182   get_logs corrupt log file in pagination loop
  220       delete_logs_before returns 0 when logs_dir doesn't exist
  224       delete_logs_before skips non-dir items
  241-242   delete_logs_before ValueError/OSError on corrupt dir name
  257       delete_schedule_logs returns 0 when logs_dir doesn't exist
  265       delete_schedule_logs skips non-dir items
  270-271   delete_schedule_logs file unlink fails
  277-278   delete_schedule_logs rmdir OSError pass
"""

import json
import pytest
from datetime import datetime, timezone, date
from pathlib import Path
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit

from app.core.scheduler.log_service import ScheduleLogService
from app.core.scheduler.models import ScheduleLogEntry, LogStep


# --- Helpers -----------------------------------------------------------------

def make_svc(tmpdir: Path) -> ScheduleLogService:
    svc = ScheduleLogService.__new__(ScheduleLogService)
    svc.logs_dir = tmpdir
    return svc


def make_entry(schedule_id="sched-001", event="PIPELINE_COMPLETED", log_id=None):
    now = datetime.now(timezone.utc)
    if log_id is None:
        tmp_svc = ScheduleLogService.__new__(ScheduleLogService)
        tmp_svc.logs_dir = Path("/tmp")
        log_id = tmp_svc._generate_log_id(schedule_id, now)
    return ScheduleLogEntry(
        log_id=log_id,
        schedule_id=schedule_id,
        timestamp=now,
        event=event,
        triggered_by="cron",
        steps=[],
        total_duration_ms=500,
    )


def write_raw_json(day_dir: Path, filename: str, content: str):
    """Write arbitrary content into a log file."""
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / filename).write_text(content, encoding="utf-8")


# =============================================================================
# Line 64 — __init__ reads settings.SCHEDULER_LOGS_DIR
# =============================================================================

class TestInit:

    @patch("app.core.scheduler.log_service.settings")
    def test_init_uses_settings_scheduler_logs_dir(self, mock_settings):
        mock_settings.SCHEDULER_LOGS_DIR = Path("/custom/logs/path")
        svc = ScheduleLogService()
        assert svc.logs_dir == Path("/custom/logs/path")


# =============================================================================
# Line 86 — _find_log_file returns None when logs_dir doesn't exist
# =============================================================================

class TestFindLogFileNoDir:

    def test_returns_none_when_logs_dir_missing(self, tmp_path):
        svc = make_svc(tmp_path / "nonexistent")
        result = svc._find_log_file("sched_abc_090000_000000")
        assert result is None


# =============================================================================
# Lines 119-121 — write_log exception branch
# =============================================================================

class TestWriteLogException:

    def test_write_log_raises_on_io_error(self, tmp_path):
        svc = make_svc(tmp_path)
        entry = make_entry()
        # Patch open to raise IOError
        with patch("builtins.open", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                svc.write_log(entry)

    def test_write_log_logs_error_before_raising(self, tmp_path):
        svc = make_svc(tmp_path)
        entry = make_entry()
        with patch("builtins.open", side_effect=OSError("disk full")), \
             patch("app.core.scheduler.log_service.logger") as mock_logger:
            with pytest.raises(OSError):
                svc.write_log(entry)
            mock_logger.error.assert_called_once()
            assert "Errore scrittura log" in mock_logger.error.call_args[0][0]


# =============================================================================
# Lines 135-137 — get_log corrupt JSON returns None
# =============================================================================

class TestGetLogCorruptJson:

    def test_returns_none_for_corrupt_json(self, tmp_path):
        svc = make_svc(tmp_path)
        day_dir = tmp_path / "2026-04-06"
        log_id = "sched_abc_100000_000000"
        write_raw_json(day_dir, f"{log_id}.json", "NOT VALID JSON {{{")
        result = svc.get_log(log_id)
        assert result is None

    def test_logs_error_for_corrupt_json(self, tmp_path):
        svc = make_svc(tmp_path)
        day_dir = tmp_path / "2026-04-06"
        log_id = "sched_abc_100000_000000"
        write_raw_json(day_dir, f"{log_id}.json", "CORRUPT")
        with patch("app.core.scheduler.log_service.logger") as mock_logger:
            svc.get_log(log_id)
            mock_logger.error.assert_called_once()
            assert "Errore lettura log" in mock_logger.error.call_args[0][0]


# =============================================================================
# Line 177 — get_logs status_filter excludes non-matching events
# =============================================================================

class TestGetLogsStatusFilter:

    def test_status_filter_includes_matching(self, tmp_path):
        svc = make_svc(tmp_path)
        entry = make_entry(event="PIPELINE_COMPLETED")
        svc.write_log(entry)
        result = svc.get_logs(entry.schedule_id, status_filter="COMPLETED")
        assert result.total == 1
        assert result.logs[0].event == "PIPELINE_COMPLETED"

    def test_status_filter_excludes_non_matching(self, tmp_path):
        svc = make_svc(tmp_path)
        entry = make_entry(event="PIPELINE_FAILED")
        svc.write_log(entry)
        result = svc.get_logs(entry.schedule_id, status_filter="COMPLETED")
        assert result.total == 0
        assert result.logs == []

    def test_status_filter_mixed(self, tmp_path):
        svc = make_svc(tmp_path)
        e1 = make_entry(log_id="sched_001_100000_000001", event="PIPELINE_COMPLETED")
        e2 = make_entry(log_id="sched_001_100000_000002", event="PIPELINE_FAILED")
        svc.write_log(e1)
        svc.write_log(e2)
        result = svc.get_logs("sched-001", status_filter="FAILED")
        assert result.total == 1
        assert result.logs[0].event == "PIPELINE_FAILED"


# =============================================================================
# Lines 181-182 — get_logs corrupt log file in iteration
# =============================================================================

class TestGetLogsCorruptFile:

    def test_skips_corrupt_files_and_continues(self, tmp_path):
        svc = make_svc(tmp_path)
        # Write one valid entry
        entry = make_entry()
        svc.write_log(entry)
        # Write a corrupt file in the same day directory
        day_str = entry.timestamp.strftime("%Y-%m-%d")
        day_dir = tmp_path / day_str
        write_raw_json(day_dir, "sched_corrupt_120000_000000.json", "NOT JSON")
        result = svc.get_logs(entry.schedule_id)
        # Valid entry still returned despite corrupt sibling
        assert result.total == 1

    def test_logs_error_for_corrupt_file_in_loop(self, tmp_path):
        svc = make_svc(tmp_path)
        day_dir = tmp_path / "2026-04-06"
        write_raw_json(day_dir, "sched_bad_120000_000000.json", "{broken")
        with patch("app.core.scheduler.log_service.logger") as mock_logger:
            svc.get_logs("sched-001")
            mock_logger.error.assert_called_once()
            assert "Errore lettura log" in mock_logger.error.call_args[0][0]


# =============================================================================
# Line 220 — delete_logs_before returns 0 when logs_dir doesn't exist
# =============================================================================

class TestDeleteLogsBeforeNoDir:

    def test_returns_zero_when_logs_dir_missing(self, tmp_path):
        svc = make_svc(tmp_path / "nonexistent")
        result = svc.delete_logs_before(date(2026, 1, 1))
        assert result == 0


# =============================================================================
# Line 224 — delete_logs_before skips non-directory items
# =============================================================================

class TestDeleteLogsBeforeSkipsFiles:

    def test_skips_regular_files_in_logs_dir(self, tmp_path):
        svc = make_svc(tmp_path)
        # Create a regular file (not a directory) inside logs_dir
        (tmp_path / "stray_file.txt").write_text("ignored")
        # Create a valid old directory that should be deleted
        old_dir = tmp_path / "2025-01-01"
        old_dir.mkdir()
        (old_dir / "sched_x_010000_000000.json").write_text("{}")
        deleted = svc.delete_logs_before(date(2026, 1, 1))
        assert deleted == 1
        # The stray file should still exist
        assert (tmp_path / "stray_file.txt").exists()


# =============================================================================
# Lines 241-242 — delete_logs_before ValueError/OSError on corrupt dir name
# =============================================================================

class TestDeleteLogsBeforeCorruptDir:

    def test_skips_dir_with_invalid_date_name(self, tmp_path):
        svc = make_svc(tmp_path)
        # Directory with unparseable name
        bad_dir = tmp_path / "not-a-date"
        bad_dir.mkdir()
        (bad_dir / "sched_x_010000_000000.json").write_text("{}")
        # Also a valid old dir
        old_dir = tmp_path / "2025-06-01"
        old_dir.mkdir()
        (old_dir / "sched_y_010000_000000.json").write_text("{}")
        deleted = svc.delete_logs_before(date(2026, 1, 1))
        # Only valid dir deleted; bad_dir survives
        assert deleted == 1
        assert bad_dir.exists()

    def test_logs_error_for_invalid_dir_name(self, tmp_path):
        svc = make_svc(tmp_path)
        bad_dir = tmp_path / "not-a-date"
        bad_dir.mkdir()
        with patch("app.core.scheduler.log_service.logger") as mock_logger:
            svc.delete_logs_before(date(2026, 1, 1))
            mock_logger.error.assert_called_once()
            assert "Errore eliminazione directory" in mock_logger.error.call_args[0][0]


# =============================================================================
# Line 257 — delete_schedule_logs returns 0 when logs_dir doesn't exist
# =============================================================================

class TestDeleteScheduleLogsNoDir:

    def test_returns_zero_when_logs_dir_missing(self, tmp_path):
        svc = make_svc(tmp_path / "nonexistent")
        result = svc.delete_schedule_logs("sched-001")
        assert result == 0


# =============================================================================
# Line 265 — delete_schedule_logs skips non-directory items
# =============================================================================

class TestDeleteScheduleLogsSkipsFiles:

    def test_skips_regular_files_in_logs_dir(self, tmp_path):
        svc = make_svc(tmp_path)
        (tmp_path / "readme.txt").write_text("ignored")
        # Write a real log so there is something to potentially match
        entry = make_entry("sched-001")
        svc.write_log(entry)
        deleted = svc.delete_schedule_logs("sched-001")
        assert deleted >= 1
        # The stray file must still exist
        assert (tmp_path / "readme.txt").exists()


# =============================================================================
# Lines 270-271 — delete_schedule_logs file unlink fails
# =============================================================================

class TestDeleteScheduleLogsUnlinkFails:

    def test_continues_on_unlink_error(self, tmp_path):
        svc = make_svc(tmp_path)
        entry = make_entry("sched-001")
        svc.write_log(entry)
        # Patch Path.unlink to fail
        with patch.object(Path, "unlink", side_effect=PermissionError("denied")):
            deleted = svc.delete_schedule_logs("sched-001")
        # unlink failed so nothing actually deleted
        assert deleted == 0

    def test_logs_error_on_unlink_failure(self, tmp_path):
        svc = make_svc(tmp_path)
        entry = make_entry("sched-001")
        svc.write_log(entry)
        with patch.object(Path, "unlink", side_effect=PermissionError("denied")), \
             patch("app.core.scheduler.log_service.logger") as mock_logger:
            svc.delete_schedule_logs("sched-001")
            # At least one error logged for the failed unlink
            assert any(
                "Errore eliminazione log" in str(call)
                for call in mock_logger.error.call_args_list
            )


# =============================================================================
# Lines 277-278 — delete_schedule_logs rmdir OSError pass (silently)
# =============================================================================

class TestDeleteScheduleLogsRmdirFails:

    def test_rmdir_oserror_is_silenced(self, tmp_path):
        svc = make_svc(tmp_path)
        entry = make_entry("sched-001")
        svc.write_log(entry)
        # Patch rmdir to raise, but unlink works fine
        original_unlink = Path.unlink
        with patch.object(Path, "rmdir", side_effect=OSError("busy")):
            deleted = svc.delete_schedule_logs("sched-001")
        # Files were deleted successfully despite rmdir failure
        assert deleted >= 1

    def test_rmdir_oserror_does_not_propagate(self, tmp_path):
        svc = make_svc(tmp_path)
        # Create a day dir with a matching log file + an extra file so dir isn't empty
        day_dir = tmp_path / "2026-04-06"
        day_dir.mkdir()
        short_id = "sched-001".replace("schedule_", "")[:12]
        (day_dir / f"sched_{short_id}_100000_000000.json").write_text("{}")
        (day_dir / "other_file.txt").write_text("keep")
        # Even though rmdir raises, no exception escapes
        with patch.object(Path, "rmdir", side_effect=OSError("not empty")):
            deleted = svc.delete_schedule_logs("sched-001")
        assert deleted == 1
        # The day dir still exists because rmdir failed (which is fine)
        assert day_dir.exists()
