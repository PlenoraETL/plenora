"""
tests/scheduler/unit/test_concurrency_manager_extra.py
======================================================
Extra unit tests for ConcurrencyManager — covers lines missed
by test_concurrency_manager.py:

  - __init__: calls _ensure_table, sets _available = True
  - _ensure_table: db.execute raising → _available = False
  - _get: corrupt expires_at (not ISO) → returns value anyway
  - _delete: calls db.execute with correct DELETE query
"""

import pytest
from unittest.mock import MagicMock, patch, call

pytestmark = pytest.mark.unit

from app.core.scheduler.concurrency_manager import ConcurrencyManager


# =============================================================================
# __init__
# =============================================================================

class TestInit:

    def test_init_calls_ensure_table_and_sets_available(self):
        """__init__ must call _ensure_table and set _available = True."""
        with patch.object(ConcurrencyManager, "_ensure_table") as mock_ensure:
            mgr = ConcurrencyManager()
        mock_ensure.assert_called_once()
        assert mgr._available is True

    def test_init_available_true_when_ensure_table_succeeds(self):
        """After successful __init__, .available property returns True."""
        with patch("app.core.scheduler.concurrency_manager.db"):
            mgr = ConcurrencyManager()
        assert mgr.available is True


# =============================================================================
# _ensure_table
# =============================================================================

class TestEnsureTable:

    def test_ensure_table_db_error_sets_available_false(self):
        """When db.execute raises inside _ensure_table, _available is set to False."""
        mgr = ConcurrencyManager.__new__(ConcurrencyManager)
        mgr._available = True  # start truthy
        with patch("app.core.scheduler.concurrency_manager.db") as mock_db:
            mock_db.execute.side_effect = RuntimeError("disk I/O error")
            mgr._ensure_table()
        assert mgr._available is False
        assert mgr.available is False

    def test_ensure_table_success_calls_create_table(self):
        """_ensure_table executes CREATE TABLE IF NOT EXISTS."""
        with patch("app.core.scheduler.concurrency_manager.db") as mock_db:
            mgr = ConcurrencyManager()
        mock_db.execute.assert_called_once()
        sql = mock_db.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS scheduler_locks" in sql


# =============================================================================
# _get — corrupt expires_at
# =============================================================================

class TestGetCorruptExpiry:

    def test_get_corrupt_expires_at_returns_value(self):
        """When expires_at is not valid ISO format, exception is caught and value is returned."""
        mgr = ConcurrencyManager.__new__(ConcurrencyManager)
        mgr._available = True
        with patch("app.core.scheduler.concurrency_manager.db") as mock_db:
            mock_db.fetchone.return_value = {
                "value": "running",
                "expires_at": "NOT-A-DATE",
            }
            result = mgr._get("scheduler:lock:sched-1")
        assert result == "running"

    def test_get_corrupt_expires_at_does_not_delete(self):
        """Corrupt expires_at must NOT trigger a DELETE (the pass branch)."""
        mgr = ConcurrencyManager.__new__(ConcurrencyManager)
        mgr._available = True
        with patch("app.core.scheduler.concurrency_manager.db") as mock_db:
            mock_db.fetchone.return_value = {
                "value": "running",
                "expires_at": "garbage-value",
            }
            mgr._get("scheduler:lock:sched-1")
        mock_db.execute.assert_not_called()


# =============================================================================
# _delete
# =============================================================================

class TestDelete:

    def test_delete_executes_correct_query(self):
        """_delete must call db.execute with DELETE ... WHERE key = ?."""
        mgr = ConcurrencyManager.__new__(ConcurrencyManager)
        mgr._available = True
        with patch("app.core.scheduler.concurrency_manager.db") as mock_db:
            mgr._delete("scheduler:lock:sched-1")
        mock_db.execute.assert_called_once_with(
            "DELETE FROM scheduler_locks WHERE key = ?",
            ("scheduler:lock:sched-1",),
        )

    def test_delete_called_with_arbitrary_key(self):
        """_delete passes the exact key it receives."""
        mgr = ConcurrencyManager.__new__(ConcurrencyManager)
        mgr._available = True
        with patch("app.core.scheduler.concurrency_manager.db") as mock_db:
            mgr._delete("scheduler:queue:abc-999")
        mock_db.execute.assert_called_once_with(
            "DELETE FROM scheduler_locks WHERE key = ?",
            ("scheduler:queue:abc-999",),
        )
