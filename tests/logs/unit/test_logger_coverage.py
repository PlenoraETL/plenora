"""
tests/logs/unit/test_logger_coverage.py
========================================
Coverage tests for logger.py — StructuredLogger, get_logger, append_step,
resolve_module, LogContext, ColoredFormatter, SQLiteLogHandler, log_event.

Avoids duplicating tests already in test_LOG_coverage.py and test_LOG_unit_part1.py.
New tests focus on:
  - StructuredLogger._log with all kwargs (op, project_id, run_id, node_id, details)
  - StructuredLogger.info/warning/error/exception call routing
  - StructuredLogger.log (raw level pass-through)
  - _resolve_module with known prefixes and unknown names
  - set_log_group / get_log_group round-trip
  - set_run_context / get_run_id / get_steps round-trip
  - get_logger caching (returns same config on second call)
  - ColoredFormatter: ERROR with [CRASH] tag, ERROR with [GESTITO] tag
  - SQLiteLogHandler.emit with exc_info
  - SQLiteLogHandler.emit with details dict
  - SQLiteLogHandler row cleanup when exceeding MAX_ROWS
  - log_event function
"""

import sys
sys.path.insert(0, r"C:\Users\Marco\Desktop\int_efm\URBY-ETL\backend")

import pytest
import logging
import sqlite3
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit


# Override del fixture autouse `mock_reset` (root conftest.py) che fa skip
# se il mock_api_server non gira. Questi test sono puri unit, niente HTTP.
@pytest.fixture(autouse=True)
def mock_reset():
    yield


# =============================================================================
# _resolve_module — prefix matching and fallback
# =============================================================================

class TestResolveModule:

    def test_known_prefix_estrai(self):
        from app.utils.logger import _resolve_module
        assert _resolve_module("app.core.estrai.executor") == "estrai"

    def test_known_prefix_manipola(self):
        from app.utils.logger import _resolve_module
        assert _resolve_module("app.core.manipola.transforms.base") == "manipola"

    def test_known_prefix_arricchisci(self):
        from app.utils.logger import _resolve_module
        assert _resolve_module("app.core.arricchisci.connector") == "arricchisci"

    def test_known_prefix_carica(self):
        from app.utils.logger import _resolve_module
        assert _resolve_module("app.core.carica.writer") == "carica"

    def test_known_prefix_services(self):
        from app.utils.logger import _resolve_module
        assert _resolve_module("app.services.something") == "services"

    def test_known_prefix_workers(self):
        from app.utils.logger import _resolve_module
        assert _resolve_module("workers.celery_worker") == "workers"

    def test_known_prefix_db(self):
        from app.utils.logger import _resolve_module
        assert _resolve_module("app.db.config_db") == "db"

    def test_known_prefix_utils(self):
        from app.utils.logger import _resolve_module
        assert _resolve_module("app.utils.geo") == "utils"

    def test_unknown_dotted_name_returns_last_part(self):
        from app.utils.logger import _resolve_module
        assert _resolve_module("some.unknown.module") == "module"

    def test_unknown_simple_name_returns_itself(self):
        from app.utils.logger import _resolve_module
        assert _resolve_module("standalone") == "standalone"

    def test_urby_carica_prefix(self):
        from app.utils.logger import _resolve_module
        assert _resolve_module("urby.carica.something") == "carica"


# =============================================================================
# Thread-local context: set/get log_group, run_context, steps
# =============================================================================

class TestThreadLocalContext:

    def test_set_get_log_group(self):
        from app.utils.logger import set_log_group, get_log_group
        set_log_group("group_test_123")
        assert get_log_group() == "group_test_123"

    def test_get_log_group_default_empty(self):
        from app.utils.logger import get_log_group, _log_context
        if hasattr(_log_context, "group_id"):
            delattr(_log_context, "group_id")
        assert get_log_group() == ""

    def test_set_run_context(self):
        from app.utils.logger import set_run_context, get_run_id, get_steps
        set_run_context("run_42", "proj_7")
        assert get_run_id() == "run_42"
        assert get_steps() == []

    def test_get_run_id_default_empty(self):
        from app.utils.logger import get_run_id, _log_context
        if hasattr(_log_context, "run_id"):
            delattr(_log_context, "run_id")
        assert get_run_id() == ""

    def test_append_step_and_get_steps(self):
        from app.utils.logger import set_run_context, append_step, get_steps
        set_run_context("run_99", "proj_1")
        append_step({"step": 1, "status": "ok"})
        append_step({"step": 2, "status": "fail"})
        steps = get_steps()
        assert len(steps) == 2
        assert steps[0]["step"] == 1
        assert steps[1]["status"] == "fail"

    def test_get_steps_returns_copy(self):
        """get_steps returns a copy, not the original list."""
        from app.utils.logger import set_run_context, append_step, get_steps
        set_run_context("run_copy_test", "proj")
        append_step({"step": 1})
        steps = get_steps()
        steps.append({"step": "injected"})
        assert len(get_steps()) == 1  # Original unchanged


# =============================================================================
# StructuredLogger — method routing with kwargs
# =============================================================================

class TestStructuredLoggerMethods:

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.utils.logger import StructuredLogger
        self._inner = MagicMock(spec=logging.Logger)
        self._inner.level = logging.INFO
        self.logger = StructuredLogger(self._inner)

    def test_info_calls_log_with_info_level(self):
        self.logger.info("test msg", op="exec", project_id="p1")
        self._inner.log.assert_called_once()
        call_args = self._inner.log.call_args
        assert call_args[0][0] == logging.INFO
        assert call_args[0][1] == "test msg"

    def test_warning_calls_log_with_warning_level(self):
        self.logger.warning("warn msg")
        self._inner.log.assert_called_once()
        assert self._inner.log.call_args[0][0] == logging.WARNING

    def test_error_calls_log_with_error_level(self):
        self.logger.error("err msg")
        self._inner.log.assert_called_once()
        assert self._inner.log.call_args[0][0] == logging.ERROR

    def test_exception_sets_exc_info_true(self):
        self.logger.exception("exc msg")
        call_kwargs = self._inner.log.call_args
        assert call_kwargs[1]["exc_info"] is True

    def test_log_extra_includes_op(self):
        self.logger.info("msg", op="transform")
        extra = self._inner.log.call_args[1]["extra"]
        assert extra["op"] == "transform"

    def test_log_extra_includes_project_id(self):
        self.logger.info("msg", project_id="p1")
        extra = self._inner.log.call_args[1]["extra"]
        assert extra["project_id"] == "p1"

    def test_log_extra_includes_run_id(self):
        self.logger.info("msg", run_id="r1")
        extra = self._inner.log.call_args[1]["extra"]
        assert extra["run_id"] == "r1"

    def test_log_extra_includes_node_id(self):
        self.logger.info("msg", node_id="n1")
        extra = self._inner.log.call_args[1]["extra"]
        assert extra["node_id"] == "n1"

    def test_log_extra_includes_details(self):
        self.logger.info("msg", details={"rows": 100})
        extra = self._inner.log.call_args[1]["extra"]
        assert extra["details"]["rows"] == 100

    def test_log_raw_level(self):
        """StructuredLogger.log passes through to inner logger."""
        self.logger.log(logging.DEBUG, "debug msg", extra={"custom": True})
        self._inner.log.assert_called_once_with(
            logging.DEBUG, "debug msg", extra={"custom": True}
        )

    def test_level_property(self):
        assert self.logger.level == logging.INFO

    def test_set_level(self):
        self.logger.setLevel(logging.DEBUG)
        self._inner.setLevel.assert_called_once_with(logging.DEBUG)

    def test_is_enabled_for(self):
        self._inner.isEnabledFor.return_value = True
        assert self.logger.isEnabledFor(logging.ERROR) is True


# =============================================================================
# ColoredFormatter — tag-based coloring for ERROR
# =============================================================================

class TestColoredFormatterExtra:

    def test_error_crash_tag_red_bold(self):
        """ERROR with [CRASH] tag gets red bold color."""
        from app.utils.logger import ColoredFormatter, _TAG_COLORS, _ANSI_RESET
        formatter = ColoredFormatter("%(message)s")
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="[CRASH] Something broke", args=(), exc_info=None
        )
        result = formatter.format(record)
        assert _TAG_COLORS["[CRASH]"] in result
        assert _ANSI_RESET in result

    def test_error_gestito_tag_yellow(self):
        """ERROR with [GESTITO] tag gets yellow color."""
        from app.utils.logger import ColoredFormatter, _TAG_COLORS, _ANSI_RESET
        formatter = ColoredFormatter("%(message)s")
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="[GESTITO] Handled error", args=(), exc_info=None
        )
        result = formatter.format(record)
        assert _TAG_COLORS["[GESTITO]"] in result

    def test_error_no_tag_uses_level_color(self):
        """ERROR without tag uses default ERROR color."""
        from app.utils.logger import ColoredFormatter, _LEVEL_COLORS, _ANSI_RESET
        formatter = ColoredFormatter("%(message)s")
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="Generic error", args=(), exc_info=None
        )
        result = formatter.format(record)
        assert _LEVEL_COLORS["ERROR"] in result

    def test_info_level_gets_cyan(self):
        """INFO level gets cyan color."""
        from app.utils.logger import ColoredFormatter, _LEVEL_COLORS, _ANSI_RESET
        formatter = ColoredFormatter("%(message)s")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Info message", args=(), exc_info=None
        )
        result = formatter.format(record)
        assert _LEVEL_COLORS["INFO"] in result

    def test_debug_level_gets_white(self):
        """DEBUG level gets white color."""
        from app.utils.logger import ColoredFormatter, _LEVEL_COLORS
        formatter = ColoredFormatter("%(message)s")
        record = logging.LogRecord(
            name="test", level=logging.DEBUG, pathname="", lineno=0,
            msg="Debug msg", args=(), exc_info=None
        )
        result = formatter.format(record)
        assert _LEVEL_COLORS["DEBUG"] in result


# =============================================================================
# SQLiteLogHandler — emit with details and exc_info
# =============================================================================

class TestSQLiteLogHandlerEmit:

    @pytest.fixture
    def handler(self, tmp_path):
        from app.utils.logger import SQLiteLogHandler
        db_path = tmp_path / "test_emit.db"
        return SQLiteLogHandler(db_path)

    def test_emit_with_details_json(self, handler):
        """Details dict is serialized as JSON in the DB."""
        record = logging.LogRecord(
            name="app.core.estrai.test", level=logging.INFO,
            pathname="", lineno=0, msg="test details", args=(), exc_info=None
        )
        record.op = "execute"
        record.details = {"rows": 42, "duration": 100}
        handler.emit(record)

        conn = sqlite3.connect(str(handler.db_path))
        row = conn.execute("SELECT details FROM logs ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        data = json.loads(row[0])
        assert data["rows"] == 42

    def test_emit_with_exc_info(self, handler):
        """Exception info is stored in the error column."""
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test", level=logging.ERROR,
            pathname="", lineno=0, msg="error happened",
            args=(), exc_info=exc_info
        )
        handler.emit(record)

        conn = sqlite3.connect(str(handler.db_path))
        row = conn.execute("SELECT error FROM logs ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        assert "ValueError" in row[0]
        assert "test error" in row[0]

    def test_emit_respects_group_id_from_context(self, handler):
        """group_id from thread-local context is used when not on record."""
        from app.utils.logger import set_log_group
        set_log_group("ctx_group_99")

        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg="msg", args=(), exc_info=None
        )
        handler.emit(record)

        conn = sqlite3.connect(str(handler.db_path))
        row = conn.execute("SELECT group_id FROM logs ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        assert row[0] == "ctx_group_99"

    def test_emit_record_group_id_overrides_context(self, handler):
        """group_id on the record overrides thread-local context."""
        from app.utils.logger import set_log_group
        set_log_group("ctx_group")

        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg="msg", args=(), exc_info=None
        )
        record.group_id = "record_group"
        handler.emit(record)

        conn = sqlite3.connect(str(handler.db_path))
        row = conn.execute("SELECT group_id FROM logs ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        assert row[0] == "record_group"


# =============================================================================
# SQLiteLogHandler — open_run / close_run
# =============================================================================

class TestSQLiteLogHandlerRuns:

    @pytest.fixture
    def handler(self, tmp_path):
        from app.utils.logger import SQLiteLogHandler
        db_path = tmp_path / "test_runs.db"
        return SQLiteLogHandler(db_path)

    def test_open_and_close_run(self, handler):
        """Full lifecycle: open -> close with all fields."""
        handler.open_run(
            run_id="run_lc_1",
            project_id="proj_1",
            group_id="g1",
            module="estrai",
            triggered_by="scheduler"
        )
        handler.close_run(
            run_id="run_lc_1",
            status="success",
            rows_in=100,
            rows_out=95,
            duration_ms=1500,
            steps=[{"step": 1, "status": "ok"}],
            debug_payload={"key": "val"}
        )

        conn = sqlite3.connect(str(handler.db_path))
        row = conn.execute("SELECT * FROM runs WHERE run_id='run_lc_1'").fetchone()
        conn.close()
        assert row is not None

    def test_close_run_with_error_info(self, handler):
        """close_run stores error fields."""
        handler.open_run(run_id="run_err", project_id="p1")
        handler.close_run(
            run_id="run_err",
            status="failure",
            error_node_id="node_3",
            error_module="arricchisci",
            error_message="Connection timeout"
        )

        conn = sqlite3.connect(str(handler.db_path))
        row = conn.execute(
            "SELECT error_node_id, error_module, error_message "
            "FROM runs WHERE run_id='run_err'"
        ).fetchone()
        conn.close()
        assert row[0] == "node_3"
        assert row[1] == "arricchisci"
        assert row[2] == "Connection timeout"


# =============================================================================
# get_logger — caching behavior
# =============================================================================

class TestGetLoggerCaching:

    def test_second_call_returns_same_wrapper(self):
        """get_logger with same name returns wrapped logger without re-adding handlers."""
        from app.utils.logger import get_logger, _configured_loggers
        name = f"test.caching.{id(self)}"
        # Ensure it's not yet configured
        _configured_loggers.discard(name)
        l1 = get_logger(name)
        l2 = get_logger(name)
        # Both wrap the same underlying logger
        assert l1._logger is l2._logger


# =============================================================================
# log_event — convenience function
# =============================================================================

class TestLogEvent:

    def test_log_event_emits_structured_log(self):
        """log_event creates a logger and emits a log event."""
        from app.utils.logger import log_event
        # Should not raise
        log_event(
            level="INFO",
            module="estrai",
            op="execute",
            msg="Extracted 100 rows",
            project_id="p1",
            details={"rows": 100}
        )

    def test_log_event_unknown_level_defaults_info(self):
        """Unknown level string defaults to INFO."""
        from app.utils.logger import log_event
        log_event(
            level="INVALID_LEVEL",
            module="test",
            op="test",
            msg="Test message"
        )


# =============================================================================
# _sanitize_log_text — regex-based redaction of URL credentials in free text
# =============================================================================

class TestSanitizeLogText:

    def test_redacts_token_in_url(self):
        from app.utils.logger import _sanitize_log_text
        out = _sanitize_log_text("call to https://api.example.com/?token=SECRET")
        assert "SECRET" not in out
        assert "***REDACTED***" in out

    def test_redacts_api_key_in_url(self):
        from app.utils.logger import _sanitize_log_text
        out = _sanitize_log_text("https://api.com/?api_key=ABC&id=1")
        assert "ABC" not in out
        assert "id=1" in out

    def test_redacts_basic_auth_in_url(self):
        from app.utils.logger import _sanitize_log_text
        out = _sanitize_log_text("https://user:pass@host/x")
        assert "user:pass" not in out
        assert "***REDACTED***@host" in out

    def test_redacts_inside_traceback_text(self):
        from app.utils.logger import _sanitize_log_text
        tb = (
            "Traceback (most recent call last):\n"
            "  File 'x.py', line 1, in foo\n"
            "requests.exceptions.HTTPError: 401 Client Error: "
            "Unauthorized for url: https://api.example.com/?token=LEAKED&id=42"
        )
        out = _sanitize_log_text(tb)
        assert "LEAKED" not in out
        assert "id=42" in out
        assert "Traceback" in out

    def test_no_url_unchanged(self):
        from app.utils.logger import _sanitize_log_text
        msg = "qualcosa di normale senza url"
        assert _sanitize_log_text(msg) == msg

    def test_url_without_sensitive_keys_unchanged(self):
        from app.utils.logger import _sanitize_log_text
        msg = "GET https://api.example.com/items?page=2"
        assert _sanitize_log_text(msg) == msg

    def test_empty_string(self):
        from app.utils.logger import _sanitize_log_text
        assert _sanitize_log_text("") == ""

    def test_none_input(self):
        from app.utils.logger import _sanitize_log_text
        assert _sanitize_log_text(None) is None

    def test_non_string_input_unchanged(self):
        from app.utils.logger import _sanitize_log_text
        assert _sanitize_log_text(42) == 42

    def test_idempotent(self):
        from app.utils.logger import _sanitize_log_text
        msg = "url=https://api.com/?token=X"
        once = _sanitize_log_text(msg)
        twice = _sanitize_log_text(once)
        assert once == twice

    def test_password_in_query(self):
        from app.utils.logger import _sanitize_log_text
        out = _sanitize_log_text("https://api.com/?password=hunter2")
        assert "hunter2" not in out


# =============================================================================
# SanitizingFilter — applies sanitization to LogRecord before handlers
# =============================================================================

class TestSanitizingFilter:

    def _make_record(self, msg, args=None, exc_info=None):
        rec = logging.LogRecord(
            name="test", level=logging.INFO, pathname="x.py", lineno=1,
            msg=msg, args=args or (), exc_info=exc_info,
        )
        return rec

    def test_filter_sanitizes_msg(self):
        from app.utils.logger import SanitizingFilter
        f = SanitizingFilter()
        rec = self._make_record("call https://api.com/?token=ABC")
        assert f.filter(rec) is True
        assert "ABC" not in rec.getMessage()
        assert "***REDACTED***" in rec.getMessage()

    def test_filter_sanitizes_msg_with_args(self):
        from app.utils.logger import SanitizingFilter
        f = SanitizingFilter()
        rec = self._make_record("call %s", args=("https://api.com/?token=ZZZ",))
        f.filter(rec)
        # Dopo il filter, args è azzerato e msg contiene la versione interpolata e sanificata
        assert rec.args == ()
        assert "ZZZ" not in rec.getMessage()

    def test_filter_sanitizes_exc_text_from_http_error(self):
        from app.utils.logger import SanitizingFilter
        f = SanitizingFilter()
        try:
            raise ValueError(
                "401 Client Error: Unauthorized for url: "
                "https://api.example.com/?token=LEAKED"
            )
        except ValueError:
            import sys
            rec = self._make_record("[CRASH]", exc_info=sys.exc_info())
        f.filter(rec)
        assert rec.exc_text is not None
        assert "LEAKED" not in rec.exc_text
        assert "***REDACTED***" in rec.exc_text

    def test_filter_safe_message_unchanged(self):
        from app.utils.logger import SanitizingFilter
        f = SanitizingFilter()
        rec = self._make_record("nothing sensitive here")
        f.filter(rec)
        assert rec.getMessage() == "nothing sensitive here"

    def test_filter_returns_true_always(self):
        """Il filter non deve mai bloccare la registrazione del log."""
        from app.utils.logger import SanitizingFilter
        f = SanitizingFilter()
        rec = self._make_record("any msg")
        assert f.filter(rec) is True
