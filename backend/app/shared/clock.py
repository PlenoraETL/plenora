"""Clock seam for time-dependent code (R-05).

Production: `WALL_CLOCK` (singleton `WallClock`) avvolge
`time.monotonic()` e `threading.Event.wait(timeout)`. Comportamento
identico al pre-R-05 - i moduli che lo adottano restano byte-per-byte
equivalenti finche' non viene iniettato un altro `Clock`.

Test: `FakeClock` permette di avanzare il tempo virtuale via
`advance(seconds)`, sbloccando deterministicamente i thread che sono
in attesa su `wait()`. Il sync e' fatto via `Event` per-call piu'
una `Condition` interna usata da `wait_for_pending_waiters()`.

Out-of-scope (R-05 ridotto al loop heartbeat):
  - `time.sleep` retry budget di `execution_manager.release_lock`:
    gia' coperto da test che mockano `app.core.scheduler.execution_manager.time.sleep`.
    Resta wall-clock in produzione, non passa per questo seam.

Go-portability: la API `now()/wait(stop, timeout)` e' minima e
replicabile in altri linguaggi (es. `interface { Now() float64;
Wait(stopCh, timeout) bool }` in Go).
"""

from __future__ import annotations

import threading
import time
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Interfaccia minima del seam clock."""

    def now(self) -> float:
        ...  # pragma: no cover

    def wait(self, stop_event: threading.Event, timeout: float) -> bool:
        """Aspetta fino a `timeout` secondi.

        Ritorna True se `stop_event` si e' settato durante l'attesa
        (richiesta di stop), False se l'attesa e' scaduta naturalmente.
        """
        ...  # pragma: no cover


class WallClock:
    """Clock di produzione: tempo reale + threading.Event.wait."""

    def now(self) -> float:
        return time.monotonic()

    def wait(self, stop_event: threading.Event, timeout: float) -> bool:
        return stop_event.wait(timeout)


# Singleton di produzione. Importato come default da chi adotta il seam.
WALL_CLOCK = WallClock()


class FakeClock:
    """Clock di test con tempo virtuale.

    Uso tipico (heartbeat thread):

        fc = FakeClock()
        hb = LockHeartbeat(..., interval=10.0, clock=fc)
        hb.start()
        fc.wait_for_pending_waiters(1)  # thread parked al primo wait
        fc.advance(11.0)                # libera il wait -> il loop ticka
        fc.wait_for_pending_waiters(1)  # thread parked di nuovo
        hb.stop()

    Semantica:
      - `now()`            : ritorna il tempo virtuale corrente.
      - `advance(s)`       : avanza il virtual time, sbloccando tutti i
                              waiters con deadline <= now.
      - `wait(stop, t)`    : registra un waiter con deadline=now+t e
                              blocca finche' (a) advance() libera la
                              deadline -> ritorna False, oppure (b)
                              `stop` si setta -> ritorna True.
      - `pending_waiters`  : numero di waiters parked correntemente.
      - `wait_for_pending_waiters(n, timeout=2.0)`: blocca (wall-clock)
                              finche' almeno `n` waiters sono parked,
                              cosi' il test puo' sincronizzarsi col
                              thread loop senza polling fragile.

    Thread-safe: `Lock` su `_now`/`_waiters`, `Condition` per il
    notify cross-thread.

    Notifier daemon (nota implementativa):
      Ogni `wait()` spawna un piccolo daemon thread che fa
      `stop_event.wait()` e poi setta il per-call event. Serve a
      sbloccare immediatamente la wait() quando il chiamante setta
      lo stop_event (es. `LockHeartbeat.stop()`). Il daemon vive
      fino a quando stop_event viene settato; se la wait() viene
      liberata da `advance()` e lo stop_event NON viene mai
      settato, il daemon resta parked finche' il processo non
      termina (pytest cleanup). Innocuo per i test heartbeat
      (sempre chiamano hb.stop() -> stop_event.set()), comportamento
      noto e accettabile per gli smoke standalone (~ms a fine test).
    """

    def __init__(self, start: float = 0.0):
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._now = float(start)
        # (deadline_virtuale, per_call_event)
        self._waiters: list[tuple[float, threading.Event]] = []

    def now(self) -> float:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("advance() richiede seconds >= 0")
        with self._lock:
            self._now += seconds
            ready = [(d, ev) for d, ev in self._waiters if d <= self._now]
            self._waiters = [(d, ev) for d, ev in self._waiters if d > self._now]
            self._cond.notify_all()
        for _d, ev in ready:
            ev.set()

    def wait(self, stop_event: threading.Event, timeout: float) -> bool:
        local = threading.Event()
        with self._lock:
            self._waiters.append((self._now + max(0.0, timeout), local))
            self._cond.notify_all()

        # Notifier daemon: se stop_event si setta durante l'attesa virtuale,
        # libera `local` per uscire subito. La stop e' idempotente:
        # `stop_event.wait()` ritorna immediatamente se gia' settato.
        # NB: se la wait() viene liberata da advance() e stop_event non viene
        # mai settato, il notifier resta parked sul stop_event.wait() fino a
        # fine processo (vedi docstring della class). Innocuo per heartbeat.
        notifier = threading.Thread(
            target=lambda: (stop_event.wait(), local.set()),
            daemon=True,
        )
        notifier.start()

        local.wait()

        with self._lock:
            # Drena: rimuovi il proprio waiter se ancora pendente
            # (caso stop_event ha sbloccato prima di advance).
            self._waiters = [w for w in self._waiters if w[1] is not local]
            self._cond.notify_all()
        return stop_event.is_set()

    @property
    def pending_waiters(self) -> int:
        with self._lock:
            return len(self._waiters)

    def wait_for_pending_waiters(self, n: int, timeout: float = 2.0) -> None:
        """Blocca (real-time, ma <2s tipico) finche' almeno `n` waiters
        sono parked. Solleva TimeoutError se non si raggiunge in tempo.
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            while len(self._waiters) < n:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"FakeClock: timeout aspettando {n} pending waiters, "
                        f"correnti: {len(self._waiters)}"
                    )
                self._cond.wait(timeout=remaining)
