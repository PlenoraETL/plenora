"""
Scheduler lock heartbeat runtime.

Keeps the lock TTL refresh thread outside ExecutionManager so the scheduler
execution flow does not own daemon/thread mechanics.
"""

from __future__ import annotations

import threading
from typing import Optional
from typing_extensions import TypedDict

from app.shared.execution.clock import Clock, WALL_CLOCK
from app.utils.logger import get_logger


logger = get_logger(__name__)


class LockHeartbeatState(TypedDict, total=False):
    """Mutable scheduler lock heartbeat state shared with ExecutionManager."""

    stop_event: threading.Event
    lock_lost: bool
    thread: threading.Thread
    _hb: object


class LockHeartbeat:
    """Daemon thread that periodically refreshes a scheduler lock."""

    def __init__(
        self,
        concurrency,
        schedule_id: str,
        interval: float,
        clock: Optional[Clock] = None,
    ):
        self._concurrency = concurrency
        self._schedule_id = schedule_id
        self._interval = interval
        self._stop_event = threading.Event()
        self._clock: Clock = clock if clock is not None else WALL_CLOCK
        self._state: LockHeartbeatState = {
            "stop_event": self._stop_event,
            "lock_lost": False,
        }
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "LockHeartbeat":
        """Spawn the daemon thread and return self for chaining."""
        thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name=f"heartbeat-{self._schedule_id}",
        )
        self._state["thread"] = thread
        self._thread = thread
        thread.start()
        return self

    def stop(self) -> None:
        """Signal stop and join the daemon with a bounded timeout."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def get_state_dict(self) -> LockHeartbeatState:
        """Return the live mutable state reference used by ExecutionManager."""
        return self._state

    @property
    def lock_lost(self) -> bool:
        """Whether refresh failed and the lock could not be re-acquired."""
        return bool(self._state.get("lock_lost", False))

    def _heartbeat_loop(self) -> None:
        while not self._clock.wait(self._stop_event, self._interval):
            try:
                ok = self._concurrency.refresh_lock(self._schedule_id)
            except Exception as e:
                logger.warning(
                    f"[HEARTBEAT] refresh_lock exception per "
                    f"{self._schedule_id}: {e}"
                )
                continue

            if ok:
                continue

            try:
                reacquired = self._concurrency.try_acquire(self._schedule_id)
            except Exception as e:
                logger.warning(
                    f"[HEARTBEAT] re-acquire exception per "
                    f"{self._schedule_id}: {e}"
                )
                continue

            if reacquired == "acquired":
                logger.warning(
                    f"[HEARTBEAT] Lock {self._schedule_id} temporaneamente "
                    f"perso, riacquisito con successo. "
                    f"Possibile blip DB/TTL expired, no concorrenza."
                )
            else:
                self._state["lock_lost"] = True
                logger.error(
                    f"[HEARTBEAT] Lock {self._schedule_id} perso e non "
                    f"riacquisibile (try_acquire={reacquired}). "
                    f"Esecuzione concorrente possibile - run marcato."
                )
