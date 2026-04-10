"""
tests/logs/unit/test_LOG_coverage.py
=====================================
Coverage tests for logger.py missing lines:
  56:  append_step when steps not initialized
  130: colorize fallback (no color match)
  199, 201: ALTER TABLE migration for run_id/node_id
  267-268: DB handler exception path
  394, 397, 400: level/setLevel/isEnabledFor
  468: open_run module function
  490: close_run module function
"""

import sys
sys.path.insert(0, r"C:\Users\Marco\Desktop\int_efm\URBY-ETL\backend")

import pytest
import logging
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.logs


# =============================================================================
# append_step — line 56: steps not initialized
# =============================================================================

class TestAppendStep:

    def test_append_step_initializes_steps_list(self):
        """Line 56: first call creates _log_context.steps."""
        from app.utils.logger import append_step, get_steps, _log_context
        # Remove steps if exists
        if hasattr(_log_context, "steps"):
            delattr(_log_context, "steps")
        append_step({"step": "TEST", "status": "ok"})
        steps = get_steps()
        assert len(steps) == 1
        assert steps[0]["step"] == "TEST"

    def test_append_step_adds_to_existing(self):
        from app.utils.logger import append_step, get_steps, _log_context
        _log_context.steps = [{"step": "A"}]
        append_step({"step": "B"})
        assert len(get_steps()) == 2


# =============================================================================
# colorize — line 130: no color match returns plain msg
# =============================================================================

class TestColorize:

    def test_no_color_match_returns_plain(self):
        """Line 130: level not in _LEVEL_COLORS returns uncolored msg."""
        from app.utils.logger import ColoredFormatter
        formatter = ColoredFormatter("%(message)s")
        # CRITICAL is NOT in _LEVEL_COLORS → hits line 130
        record = logging.LogRecord(
            name="test", level=logging.CRITICAL, pathname="", lineno=0,
            msg="plain message", args=(), exc_info=None
        )
        result = formatter.format(record)
        assert result == "plain message"


# =============================================================================
# ALTER TABLE migration — lines 199, 201
# =============================================================================

class TestDbMigration:

    def test_migration_adds_run_id_column(self, tmp_path):
        """Line 199: ALTER TABLE adds run_id when missing."""
        import sqlite3
        db_path = tmp_path / "test_log.db"
        # Create table WITHOUT run_id/node_id columns
        conn = sqlite3.connect(str(db_path))
        # Old schema WITHOUT run_id/node_id to test migration
        conn.execute("""
            CREATE TABLE logs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         TEXT NOT NULL,
                level      TEXT NOT NULL,
                module     TEXT NOT NULL,
                op         TEXT DEFAULT '',
                msg        TEXT NOT NULL,
                project_id TEXT DEFAULT '',
                group_id   TEXT DEFAULT '',
                details    TEXT DEFAULT '',
                error      TEXT DEFAULT ''
            )
        """)
        conn.commit()
        conn.close()

        # Now create handler which should migrate
        from app.utils.logger import SQLiteLogHandler
        with patch("app.utils.logger.LOG_DB_FILE", str(db_path)):
            handler = SQLiteLogHandler.__new__(SQLiteLogHandler)
            handler.db_path = str(db_path)
            handler._init_db()

        # Verify columns were added
        conn = sqlite3.connect(str(db_path))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(logs)")}
        conn.close()
        assert "run_id" in cols
        assert "node_id" in cols


# =============================================================================
# DB handler exception — lines 267-268
# =============================================================================

class TestDbHandlerException:

    def test_emit_exception_calls_handle_error(self):
        """Lines 267-268: exception in emit triggers handleError."""
        from app.utils.logger import SQLiteLogHandler
        handler = SQLiteLogHandler.__new__(SQLiteLogHandler)
        handler.db_path = "/nonexistent/path/log.db"
        handler.handleError = MagicMock()

        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test", args=(), exc_info=None
        )
        # This should fail (no DB) and call handleError
        handler.emit(record)
        handler.handleError.assert_called_once_with(record)


# =============================================================================
# level, setLevel, isEnabledFor — lines 394, 397, 400
# =============================================================================

class TestLoggerProperties:

    def test_level_property(self):
        """Line 394: level property."""
        from app.utils.logger import get_logger
        logger = get_logger("test.level.prop")
        level = logger.level
        assert isinstance(level, int)

    def test_set_level(self):
        """Line 397: setLevel."""
        from app.utils.logger import get_logger
        logger = get_logger("test.set.level")
        logger.setLevel(logging.WARNING)
        assert logger.level == logging.WARNING

    def test_is_enabled_for(self):
        """Line 400: isEnabledFor."""
        from app.utils.logger import get_logger
        logger = get_logger("test.enabled.for")
        logger.setLevel(logging.WARNING)
        assert logger.isEnabledFor(logging.ERROR) is True
        assert logger.isEnabledFor(logging.DEBUG) is False


# =============================================================================
# open_run / close_run module functions — lines 468, 490
# =============================================================================

class TestModuleFunctions:

    def test_open_run_delegates_to_handler(self):
        """Line 468: open_run() calls _get_db_handler().open_run()."""
        from app.utils.logger import open_run
        with patch("app.utils.logger._get_db_handler") as mock_get:
            mock_handler = MagicMock()
            mock_get.return_value = mock_handler
            open_run(
                run_id="run-1",
                project_id="proj-1",
                group_id="g1",
                module="estrai",
                triggered_by="manual",
            )
        mock_handler.open_run.assert_called_once_with(
            run_id="run-1",
            project_id="proj-1",
            group_id="g1",
            module="estrai",
            triggered_by="manual",
        )

    def test_close_run_delegates_to_handler(self):
        """Line 490: close_run() calls _get_db_handler().close_run()."""
        from app.utils.logger import close_run
        with patch("app.utils.logger._get_db_handler") as mock_get:
            mock_handler = MagicMock()
            mock_get.return_value = mock_handler
            close_run(
                run_id="run-1",
                status="success",
                rows_in=100,
                rows_out=95,
                duration_ms=1500,
            )
        mock_handler.close_run.assert_called_once()
        call_kwargs = mock_handler.close_run.call_args[1]
        assert call_kwargs["run_id"] == "run-1"
        assert call_kwargs["status"] == "success"
        assert call_kwargs["rows_in"] == 100
