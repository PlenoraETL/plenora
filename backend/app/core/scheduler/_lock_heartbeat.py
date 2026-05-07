"""
LockHeartbeat — class per il TTL refresh thread del lock scheduler

(PR-#36 REFACTOR-EXEC-MGR c1).
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from app.shared.clock import Clock, WALL_CLOCK
from app.utils.logger import get_logger


logger = get_logger(__name__)


class LockHeartbeat:
    """Thread daemon che rinfresca periodicamente il lock scheduler.

    Uso (wrapper compat — pattern attuale):
        hb = LockHeartbeat(concurrency, schedule_id, interval)
        hb.start()
        state = hb.get_state_dict()  # reference live, NON copia
        ...
        hb.stop()  # set stop_event + join(timeout=5s)
    """

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
        # R-05 seam: in produzione usa WALL_CLOCK (stop_event.wait); i test
        # iniettano FakeClock per avanzare il tempo deterministicamente.
        self._clock: Clock = clock if clock is not None else WALL_CLOCK
        self._state: Dict[str, Any] = {
            "stop_event": self._stop_event,
            "lock_lost": False,
        }
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "LockHeartbeat":
        """Spawn del thread daemon. Ritorna self per chaining."""
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
        """Segnala stop al thread + join(timeout=5s). Idempotente."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def get_state_dict(self) -> Dict[str, Any]:
        """Ritorna la **reference** al dict interno mutabile.

        Non e' una copia: mutazioni del thread loop (es.
        `state["lock_lost"] = True`) sono visibili al chiamante
        immediatamente. Questo e' essenziale per preservare il contract
        pubblico del wrapper compat in `execution_manager`.

        ACCOPPIAMENTO NOTO (Finding H, PR-#36):
        Il wrapper compat `execution_manager._start_lock_heartbeat`
        aggiunge la chiave `"_hb"` (reference a self) al dict ritornato
        da questo metodo. Poiche' `_state` E' la stessa reference, il
        wrapper di fatto MUTA `LockHeartbeat._state` aggiungendo
        `_state["_hb"] = self`. E' una leaky abstraction documentata,
        non un bug: l'unica alternativa (dict copia) perderebbe la
        reference live su `lock_lost` e romperebbe il contract verso
        `execute()`. Risoluzione completa richiede c2 (uso del context
        manager direttamente in `execute()`), che e' deferred.

        Effetti collaterali del ciclo `_state["_hb"] = self`:
          - GC: Python gestisce cicli, no leak reale
          - lettori del dict: vedono una chiave extra `_hb` oltre a
            `stop_event`, `thread`, `lock_lost`. `execute()` legge solo
            `lock_lost`, ignora il resto: safe
          - test che assert su shape esatta del dict devono prevedere
            `_hb` se istanziato via wrapper, NON se istanziato diretto
        """
        return self._state

    @property
    def lock_lost(self) -> bool:
        """True se il thread loop ha rilevato perdita del lock e
        re-acquire fallito. Read-only, mutato dal solo thread loop."""
        return bool(self._state.get("lock_lost", False))

    # Internal loop — copiato 1:1 da execution_manager._start_lock_heartbeat
    # (line 152-200 pre-refactor). Niente behavior change.

    def _heartbeat_loop(self) -> None:
        # Usa clock.wait(stop_event, timeout): in produzione e' equivalente
        # a stop_event.wait(timeout) (singleton WALL_CLOCK), nei test e'
        # FakeClock che avanza il tempo virtuale via advance(). Lo stop
        # resta deterministico (set() del stop_event sblocca subito).
        while not self._clock.wait(self._stop_event, self._interval):
            try:
                ok = self._concurrency.refresh_lock(self._schedule_id)
            except Exception as e:
                # Transient DB blip: logga warning ma continua
                # (il prossimo tick riprovera').
                logger.warning(
                    f"[HEARTBEAT] refresh_lock exception per "
                    f"{self._schedule_id}: {e}"
                )
                continue

            if ok:
                continue

            # refresh_lock ritornato False: il lock e' scaduto o
            # rilasciato. Tenta re-acquire — se riesce, nessuno ha
            # ripreso nel frattempo; se fallisce, un altro processo
            # sta potenzialmente eseguendo lo stesso schedule.
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
                # Un altro processo detiene il lock. Segnaliamo
                # nello state cosi' execute() puo' portarlo nel
                # result come warning di esecuzione non piu' protetta.
                self._state["lock_lost"] = True
                logger.error(
                    f"[HEARTBEAT] Lock {self._schedule_id} perso e non "
                    f"riacquisibile (try_acquire={reacquired}). "
                    f"Esecuzione concorrente possibile — run marcato."
                )
