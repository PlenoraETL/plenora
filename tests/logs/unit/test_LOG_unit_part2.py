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
# LOG_EVENT
# =============================================================================

class TestLogEvent:

    @pytest.mark.parametrize("level", ["INFO", "WARNING", "ERROR"])
    def test_log_event_vari_livelli(self, level):
        from app.utils.logger import log_event
        log_event(level, "test", "test_op", f"test {level}")

    @pytest.mark.parametrize("module", [
        "estrai", "manipola", "arricchisci", "carica", "scheduler"
    ])
    def test_log_event_vari_moduli(self, module):
        from app.utils.logger import log_event
        log_event("INFO", module, "execute", f"test {module}")

    def test_log_event_con_project_id(self):
        from app.utils.logger import log_event
        log_event("INFO", "estrai", "execute", "test",
                  project_id="proj-test-123")

    def test_log_event_con_details(self):
        from app.utils.logger import log_event
        log_event("INFO", "manipola", "execute", "test",
                  details={"rows": 100})

    @pytest.mark.parametrize("op", [
        "execute", "preview", "connect", "upload",
        "enrich", "load", "schedule"
    ])
    def test_log_event_varie_op(self, op):
        from app.utils.logger import log_event
        log_event("INFO", "estrai", op, f"test op {op}")
