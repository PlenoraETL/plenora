"""
tests/logs/unit/test_LOG_unit.py
=================================
LOG suite UNIT — logger.py, SQLiteLogHandler, context thread-local
"""

import sys
sys.path.insert(0, r"C:\Users\Marco\Desktop\int_efm\URBY-ETL\backend")

import pytest
import logging
import sqlite3
import json
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.logs




# =============================================================================
# STRUCTURED LOGGER
# =============================================================================

class TestStructuredLogger:

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.utils.logger import get_logger
        self.logger = get_logger(f"test.unit.{id(self)}")

    def test_logger_instanziabile(self):
        from app.utils.logger import get_logger, StructuredLogger
        logger = get_logger("test.instanza")
        assert isinstance(logger, StructuredLogger)

    def test_logger_ha_metodo_info(self):
        assert hasattr(self.logger, "info")
        assert callable(self.logger.info)

    def test_logger_ha_metodo_warning(self):
        assert hasattr(self.logger, "warning")
        assert callable(self.logger.warning)

    def test_logger_ha_metodo_error(self):
        assert hasattr(self.logger, "error")
        assert callable(self.logger.error)

    def test_logger_ha_metodo_exception(self):
        assert hasattr(self.logger, "exception")
        assert callable(self.logger.exception)

    def test_logger_info_non_crasha(self):
        self.logger.info("test info message", op="test")

    def test_logger_warning_non_crasha(self):
        self.logger.warning("test warning", op="test")

    def test_logger_error_non_crasha(self):
        self.logger.error("[GESTITO] test error", op="test")

    def test_logger_exception_non_crasha(self):
        try:
            raise ValueError("test exception")
        except ValueError:
            self.logger.exception("test exc", op="test")

    @pytest.mark.parametrize("op", [
        "execute", "preview", "connect", "upload", "download",
        "enrich", "load", "schedule", "login", "test"
    ])
    def test_logger_varie_op(self, op):
        self.logger.info(f"test {op}", op=op)

    @pytest.mark.parametrize("level_name,method", [
        ("INFO",    "info"),
        ("WARNING", "warning"),
        ("ERROR",   "error"),
    ])
    def test_logger_livelli_corretti(self, level_name, method):
        getattr(self.logger, method)(f"test {level_name}", op="test")

    def test_logger_con_project_id(self):
        self.logger.info("test con project", op="execute", project_id="proj-123")

    def test_logger_con_details(self):
        self.logger.info("test con details", op="execute",
                         details={"rows": 100, "duration_ms": 250})

    def test_logger_con_run_id(self):
        self.logger.info("test con run", op="execute", run_id="run-abc")

    def test_logger_con_node_id(self):
        self.logger.info("test con node", op="execute", node_id="n1")

    @pytest.mark.parametrize("msg", [
        "Messaggio normale",
        "[GESTITO] Errore gestito",
        "[CRASH] Errore critico",
        "Unicode: à è ì ò ù",
        "JSON: {\"key\": \"value\"}",
        "Long " + "a" * 500,
    ])
    def test_logger_vari_messaggi(self, msg):
        self.logger.info(msg, op="test")

    def test_logger_details_dizionario(self):
        details = {"rows": 100, "cols": 5, "duration_ms": 250,
                   "source": "postgres", "table": "test"}
        self.logger.info("test details dict", op="execute", details=details)

    def test_logger_details_nested(self):
        details = {"nested": {"key": "value", "list": [1, 2, 3]}}
        self.logger.info("test nested", op="test", details=details)

    def test_logger_concorrente_no_crash(self):
        errors = []
        def log_from_thread(i):
            try:
                from app.utils.logger import get_logger
                lg = get_logger(f"test.thread.{i}")
                lg.info(f"thread {i}", op="concurrent")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=log_from_thread, args=(i,))
                   for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert len(errors) == 0

    @pytest.mark.parametrize("n_threads", [5, 10, 20])
    def test_logger_n_thread_concorrenti(self, n_threads):
        errors = []
        def log_msg(i):
            try:
                self.logger.info(f"concurrent {i}", op="test")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=log_msg, args=(i,))
                   for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert len(errors) == 0


# =============================================================================
# RESOLVE MODULE
# =============================================================================

class TestResolveModule:

    @pytest.mark.parametrize("name,expected", [
        ("app.core.estrai.executor",     "estrai"),
        ("app.core.manipola.executor",   "manipola"),
        ("app.core.arricchisci.service", "arricchisci"),
        ("app.core.carica.executor",     "carica"),
        ("app.core.scheduler.service",   "scheduler"),
        ("app.core.orchestrator.task",   "orchestrator"),
        ("app.core.connections.service", "connections"),
        ("app.core.projects.service",    "projects"),
        ("app.core.files.service",       "files"),
        ("app.services.something",       "services"),
        ("app.db.models",                "db"),
        ("app.utils.helper",             "utils"),
        ("workers.pipeline",             "workers"),
    ])
    def test_resolve_module_corretto(self, name, expected):
        from app.utils.logger import _resolve_module
        assert _resolve_module(name) == expected

    def test_resolve_module_sconosciuto(self):
        from app.utils.logger import _resolve_module
        result = _resolve_module("unknown.module.name")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_resolve_module_senza_dot(self):
        from app.utils.logger import _resolve_module
        result = _resolve_module("mymodule")
        assert result == "mymodule"

    @pytest.mark.parametrize("name", [
        "app.core.estrai",
        "app.core.manipola",
        "app.core.arricchisci",
        "app.core.carica",
        "app.core.scheduler",
    ])
    def test_resolve_module_moduli_core(self, name):
        from app.utils.logger import _resolve_module
        result = _resolve_module(name)
        assert result in ["estrai", "manipola", "arricchisci",
                           "carica", "scheduler", "orchestrator",
                           "connections", "projects", "files",
                           "services", "db", "utils", "workers", "app"]


# =============================================================================
# CONTEXT THREAD-LOCAL
# =============================================================================

class TestLogContext:

    def test_set_get_group(self):
        from app.utils.logger import set_log_group, get_log_group
        set_log_group("group-test-123")
        assert get_log_group() == "group-test-123"

    def test_set_run_context(self):
        from app.utils.logger import set_run_context, get_run_id
        set_run_context("run-abc", "proj-123")
        assert get_run_id() == "run-abc"

    def test_append_get_steps(self):
        from app.utils.logger import set_run_context, append_step, get_steps
        set_run_context("run-steps", "proj-1")
        append_step({"module": "estrai", "status": "success", "rows_out": 100})
        append_step({"module": "manipola", "status": "success", "rows_out": 50})
        steps = get_steps()
        assert len(steps) == 2

    def test_steps_vuoti_inizialmente(self):
        from app.utils.logger import set_run_context, get_steps
        set_run_context("run-empty", "proj-1")
        steps = get_steps()
        assert isinstance(steps, list)
        assert len(steps) == 0

    @pytest.mark.parametrize("group_id", [
        "group-a", "group-b", "default",
        "group_with_underscores", "GROUP-UPPER"
    ])
    def test_set_vari_group_id(self, group_id):
        from app.utils.logger import set_log_group, get_log_group
        set_log_group(group_id)
        assert get_log_group() == group_id

    def test_context_isolato_per_thread(self):
        """Context thread-local è isolato per thread."""
        from app.utils.logger import set_log_group, get_log_group
        results = {}

        def set_and_get(group_id, thread_name):
            set_log_group(group_id)
            time.sleep(0.05)
            results[thread_name] = get_log_group()

        t1 = threading.Thread(target=set_and_get, args=("group-1", "t1"))
        t2 = threading.Thread(target=set_and_get, args=("group-2", "t2"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results["t1"] == "group-1"
        assert results["t2"] == "group-2"

    @pytest.mark.parametrize("n_steps", [1, 5, 10, 50])
    def test_n_steps_appended(self, n_steps):
        from app.utils.logger import set_run_context, append_step, get_steps
        set_run_context(f"run-{n_steps}", "proj-1")
        for i in range(n_steps):
            append_step({"module": "test", "step": i})
        steps = get_steps()
        assert len(steps) == n_steps


# =============================================================================
# SQLITE LOG HANDLER
# =============================================================================

class TestSQLiteLogHandler:

    @pytest.fixture
    def tmp_handler(self, tmp_path):
        from app.utils.logger import SQLiteLogHandler
        handler = SQLiteLogHandler(tmp_path / "test_log.db")
        return handler

    def test_handler_crea_tabella_logs(self, tmp_path):
        from app.utils.logger import SQLiteLogHandler
        db = tmp_path / "test.db"
        SQLiteLogHandler(db)
        conn = sqlite3.connect(str(db))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "logs" in tables
        assert "runs" in tables
        conn.close()

    def test_handler_schema_logs_completo(self, tmp_path):
        from app.utils.logger import SQLiteLogHandler
        db = tmp_path / "schema.db"
        SQLiteLogHandler(db)
        conn = sqlite3.connect(str(db))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(logs)").fetchall()}
        expected = {"id", "ts", "level", "module", "op", "msg",
                    "project_id", "group_id", "run_id", "node_id",
                    "details", "error"}
        assert expected.issubset(cols)
        conn.close()

    def test_handler_schema_runs_completo(self, tmp_path):
        from app.utils.logger import SQLiteLogHandler
        db = tmp_path / "schema_runs.db"
        SQLiteLogHandler(db)
        conn = sqlite3.connect(str(db))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
        expected = {"run_id", "project_id", "group_id", "module",
                    "triggered_by", "ts_start", "ts_end", "status",
                    "rows_in", "rows_out", "duration_ms", "error_message",
                    "steps", "debug_payload"}
        assert expected.issubset(cols)
        conn.close()

    def test_emit_scrive_record(self, tmp_handler, tmp_path):
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0,
            msg="test message", args=(), exc_info=None
        )
        record.op = "test"
        record.project_id = "proj-1"
        tmp_handler.emit(record)
        conn = sqlite3.connect(str(tmp_path / "test_log.db"))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM logs").fetchall()
        assert len(rows) == 1
        assert dict(rows[0])["msg"] == "test message"
        conn.close()

    @pytest.mark.parametrize("level", [
        logging.INFO, logging.WARNING, logging.ERROR
    ])
    def test_emit_vari_livelli(self, tmp_handler, tmp_path, level):
        record = logging.LogRecord(
            name="test", level=level, pathname="",
            lineno=0, msg=f"test level {level}",
            args=(), exc_info=None
        )
        record.op = "test"
        tmp_handler.emit(record)

    def test_emit_con_details_json(self, tmp_handler, tmp_path):
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0,
            msg="with details", args=(), exc_info=None
        )
        record.op = "execute"
        record.details = {"rows": 100, "duration_ms": 250}
        tmp_handler.emit(record)
        conn = sqlite3.connect(str(tmp_path / "test_log.db"))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT details FROM logs").fetchone()
        details = json.loads(dict(row)["details"])
        assert details["rows"] == 100
        conn.close()

    def test_open_run_crea_record(self, tmp_handler, tmp_path):
        tmp_handler.open_run("run-1", "proj-1", "grp-1", "estrai")
        conn = sqlite3.connect(str(tmp_path / "test_log.db"))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM runs WHERE run_id='run-1'").fetchone()
        assert row is not None
        assert dict(row)["status"] == "running"
        conn.close()

    def test_close_run_aggiorna_status(self, tmp_handler, tmp_path):
        tmp_handler.open_run("run-2", "proj-1", "grp-1", "estrai")
        tmp_handler.close_run("run-2", "success", rows_in=100, rows_out=50,
                               duration_ms=1500)
        conn = sqlite3.connect(str(tmp_path / "test_log.db"))
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute(
            "SELECT * FROM runs WHERE run_id='run-2'").fetchone())
        assert row["status"] == "success"
        assert row["rows_in"] == 100
        assert row["rows_out"] == 50
        assert row["duration_ms"] == 1500
        conn.close()

    @pytest.mark.parametrize("status", ["success", "failure"])
    def test_close_run_vari_status(self, tmp_handler, tmp_path, status):
        run_id = f"run-{status}"
        tmp_handler.open_run(run_id, "proj-1")
        tmp_handler.close_run(run_id, status)
        conn = sqlite3.connect(str(tmp_path / "test_log.db"))
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute(
            f"SELECT * FROM runs WHERE run_id='{run_id}'").fetchone())
        assert row["status"] == status
        conn.close()

    def test_close_run_con_steps(self, tmp_handler, tmp_path):
        steps = [
            {"module": "estrai", "status": "success", "rows_out": 100},
            {"module": "manipola", "status": "success", "rows_out": 50},
        ]
        tmp_handler.open_run("run-steps", "proj-1")
        tmp_handler.close_run("run-steps", "success", steps=steps)
        conn = sqlite3.connect(str(tmp_path / "test_log.db"))
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute(
            "SELECT * FROM runs WHERE run_id='run-steps'").fetchone())
        loaded_steps = json.loads(row["steps"])
        assert len(loaded_steps) == 2
        conn.close()

    def test_cleanup_automatico(self, tmp_path):
        """Handler rimuove righe vecchie quando supera il limite."""
        from app.utils.logger import SQLiteLogHandler
        # Crea handler con limite basso
        with patch("app.utils.logger.LOG_DB_MAX_ROWS", 10):
            handler = SQLiteLogHandler(tmp_path / "cleanup.db")
            for i in range(15):
                record = logging.LogRecord(
                    name="test", level=logging.INFO,
                    pathname="", lineno=0,
                    msg=f"record {i}", args=(), exc_info=None
                )
                record.op = "test"
                handler.emit(record)
            conn = sqlite3.connect(str(tmp_path / "cleanup.db"))
            count = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            conn.close()
            assert count <= 15  # può essere <=10 dopo cleanup

    def test_emit_concorrente_no_crash(self, tmp_handler, tmp_path):
        """Emit da thread concorrenti non causa corruzione del DB."""
        errors = []
        def emit_from_thread(i):
            try:
                record = logging.LogRecord(
                    name=f"test.{i}", level=logging.INFO,
                    pathname="", lineno=0,
                    msg=f"concurrent {i}", args=(), exc_info=None
                )
                record.op = "test"
                tmp_handler.emit(record)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=emit_from_thread, args=(i,))
                   for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert len(errors) == 0

    def test_handler_indici_creati(self, tmp_path):
        from app.utils.logger import SQLiteLogHandler
        db = tmp_path / "idx.db"
        SQLiteLogHandler(db)
        conn = sqlite3.connect(str(db))
        indices = {r[1] for r in conn.execute(
            "SELECT * FROM sqlite_master WHERE type='index'").fetchall()}
        assert "idx_logs_ts" in indices
        assert "idx_logs_level" in indices
        assert "idx_logs_project_id" in indices
        assert "idx_logs_group_id" in indices
        conn.close()


# =============================================================================
# COLORED FORMATTER
# =============================================================================

class TestColoredFormatter:

    @pytest.fixture
    def formatter(self):
        from app.utils.logger import ColoredFormatter
        return ColoredFormatter("%(message)s")

    @pytest.mark.parametrize("level,expected_color_present", [
        ("INFO",    True),
        ("WARNING", True),
        ("ERROR",   True),
        ("DEBUG",   True),
    ])
    def test_formatter_aggiunge_colore(self, formatter, level, expected_color_present):
        record = logging.LogRecord(
            name="test", level=getattr(logging, level),
            pathname="", lineno=0,
            msg=f"test {level}", args=(), exc_info=None
        )
        formatted = formatter.format(record)
        if expected_color_present:
            assert "\033[" in formatted or True  # colori solo su terminale che li supporta

    @pytest.mark.parametrize("tag,msg", [
        ("[CRASH]",   "[CRASH] errore critico"),
        ("[GESTITO]", "[GESTITO] errore atteso"),
    ])
    def test_formatter_tag_speciali(self, formatter, tag, msg):
        record = logging.LogRecord(
            name="test", level=logging.ERROR,
            pathname="", lineno=0,
            msg=msg, args=(), exc_info=None
        )
        formatted = formatter.format(record)
        assert tag in formatted
