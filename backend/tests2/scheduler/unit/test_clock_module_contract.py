"""
Smoke contract - R-05: app.shared.execution.clock.

Verifica le invarianti minime del boundary:
"""

from __future__ import annotations

import threading
import time

import pytest

from app.shared.execution.clock import WALL_CLOCK, Clock, FakeClock, WallClock


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


# WallClock production


def test_wall_clock_is_singleton_and_protocol_compliant():
    assert isinstance(WALL_CLOCK, WallClock)
    assert isinstance(WALL_CLOCK, Clock)


def test_wall_clock_now_is_monotonic():
    a = WALL_CLOCK.now()
    b = WALL_CLOCK.now()
    assert b >= a


def test_wall_clock_wait_returns_true_when_stop_set_immediately():
    ev = threading.Event()
    ev.set()
    t0 = time.monotonic()
    result = WALL_CLOCK.wait(ev, timeout=10.0)
    elapsed = time.monotonic() - t0
    assert result is True
    assert elapsed < 0.1, "stop gia' settato deve sbloccare subito, non attendere"


def test_wall_clock_wait_returns_false_on_timeout():
    ev = threading.Event()
    t0 = time.monotonic()
    result = WALL_CLOCK.wait(ev, timeout=0.05)
    elapsed = time.monotonic() - t0
    assert result is False
    assert elapsed >= 0.04, "deve aspettare quasi tutto il timeout"


# FakeClock test-only


def test_fake_clock_now_starts_at_zero_and_advances():
    fc = FakeClock()
    assert fc.now() == 0.0
    fc.advance(5.0)
    assert fc.now() == 5.0
    fc.advance(2.5)
    assert fc.now() == 7.5


def test_fake_clock_advance_negative_raises():
    fc = FakeClock()
    with pytest.raises(ValueError):
        fc.advance(-1.0)


def test_fake_clock_is_protocol_compliant():
    assert isinstance(FakeClock(), Clock)


def test_fake_clock_wait_releases_on_advance_past_deadline():
    fc = FakeClock()
    ev = threading.Event()
    result = {"value": None}

    def waiter():
        result["value"] = fc.wait(ev, timeout=10.0)

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    fc.wait_for_pending_waiters(1)
    fc.advance(10.5)
    t.join(timeout=1.0)
    assert not t.is_alive(), "advance() deve aver liberato il waiter"
    assert result["value"] is False, "timeout virtuale -> ritorno False"


def test_fake_clock_wait_returns_true_when_stop_event_is_set():
    fc = FakeClock()
    ev = threading.Event()
    result = {"value": None}

    def waiter():
        result["value"] = fc.wait(ev, timeout=10.0)

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    fc.wait_for_pending_waiters(1)
    ev.set()
    t.join(timeout=1.0)
    assert not t.is_alive()
    assert result["value"] is True


def test_fake_clock_pending_waiters_count_and_drain_after_advance():
    fc = FakeClock()
    ev = threading.Event()
    threads = []
    for _ in range(3):
        th = threading.Thread(target=lambda: fc.wait(ev, timeout=5.0), daemon=True)
        th.start()
        threads.append(th)
    fc.wait_for_pending_waiters(3)
    assert fc.pending_waiters == 3
    fc.advance(6.0)
    for th in threads:
        th.join(timeout=1.0)
    assert fc.pending_waiters == 0


def test_fake_clock_wait_for_pending_waiters_timeout_raises():
    fc = FakeClock()
    with pytest.raises(TimeoutError):
        fc.wait_for_pending_waiters(1, timeout=0.1)
