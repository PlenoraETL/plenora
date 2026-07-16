from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.core.scheduler.concurrency_manager import ConcurrencyManager


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.regression]


class _Cursor:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


def test_owner_token_prevents_stale_refresh_and_release() -> None:
    manager = ConcurrencyManager.__new__(ConcurrencyManager)
    current = {"owner": "worker-b"}

    def execute(sql: str, params: tuple):
        if sql.startswith("UPDATE"):
            _expires, _key, owner = params
            return _Cursor(1 if owner == current["owner"] else 0)
        if sql.startswith("DELETE"):
            _key, owner = params
            if owner == current["owner"]:
                current["owner"] = None
                return _Cursor(1)
            return _Cursor(0)
        raise AssertionError(sql)

    with patch("app.core.scheduler.concurrency_manager.db.execute", side_effect=execute):
        assert manager.refresh_lock("schedule-1", owner_id="worker-a") is False
        assert manager.release("schedule-1", owner_id="worker-a") is False
        assert current["owner"] == "worker-b"
        assert manager.refresh_lock("schedule-1", owner_id="worker-b") is True
        assert manager.release("schedule-1", owner_id="worker-b") is True
        assert current["owner"] is None


def test_reserved_retry_reuses_one_queue_slot_and_consumes_it_on_acquire() -> None:
    manager = ConcurrencyManager.__new__(ConcurrencyManager)
    manager._set_nx = MagicMock(side_effect=[False, True])
    manager._incr = MagicMock()
    manager._decr = MagicMock(return_value=0)

    assert manager.try_acquire(
        "schedule-1",
        owner_id="run-1",
        queue_reserved=True,
    ) == "queued"
    manager._incr.assert_not_called()
    manager._decr.assert_not_called()

    assert manager.try_acquire(
        "schedule-1",
        owner_id="run-2",
        queue_reserved=True,
    ) == "acquired"
    manager._decr.assert_called_once_with("scheduler:queue:schedule-1")


def test_heartbeat_reacquire_never_creates_queue_reservation() -> None:
    manager = ConcurrencyManager.__new__(ConcurrencyManager)
    manager._set_nx = MagicMock(return_value=False)
    manager._incr = MagicMock()

    result = manager.try_acquire(
        "schedule-1",
        owner_id="run-1",
        queue_on_busy=False,
    )

    assert result == "skipped"
    manager._incr.assert_not_called()
