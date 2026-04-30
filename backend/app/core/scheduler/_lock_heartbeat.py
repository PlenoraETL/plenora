"""LockHeartbeat — context manager + class per il TTL refresh thread
del lock scheduler (PR-#36 REFACTOR-EXEC-MGR c1).

Motivazione (estratta da `execution_manager._start_lock_heartbeat`):
il TTL del lock scheduler e' corto (10min) per permettere recovery
veloce su SIGKILL del worker. Finche' la pipeline e' attiva, il worker
rinfresca il TTL via un thread daemon. Se il worker muore, il thread
daemon muore con lui, non rinfresca piu', e il lock scade entro il
TTL invece di restare appeso.

Lock lost handling (C3 follow-up fix A): se `refresh_lock` ritorna
False (lock scaduto/rilasciato), il thread tenta re-acquire atomico.
Se riesce → WARNING, continua. Se fallisce → ERROR + flag
`state["lock_lost"] = True` cosi' `execute()` puo' includere il warning
nel risultato.

Design (PR-#36):
  - `LockHeartbeat.start()` spawn del thread daemon
  - `LockHeartbeat.stop()` set stop_event + join(timeout=5s), idempotente
  - `LockHeartbeat.get_state_dict()` ritorna la **reference** del dict
    interno mutabile (non copia) cosi' `execute()` legge live le
    mutazioni di `state["lock_lost"]` dal thread loop
  - Supporta `with LockHeartbeat(...) as hb: hb.lock_lost` per uso
    futuro (c2), non usato ancora dal wrapper compat di execution_manager

Wrapper compat in `execution_manager._start_lock_heartbeat` /
`_stop_lock_heartbeat` preserva la firma pubblica e restituisce un
dict shape `{stop_event, thread, lock_lost, _hb}` dove `_hb` e' la
reference opaca al `LockHeartbeat` (consumata solo da
`_stop_lock_heartbeat`, NON da `execute()`).
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

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

    Uso (futuro c2 — context manager):
        with LockHeartbeat(concurrency, schedule_id, interval) as hb:
            ...
            if hb.lock_lost: ...
        # stop automatico nel __exit__
    """

    def __init__(
        self,
        concurrency,
        schedule_id: str,
        interval: float,
    ):
        self._concurrency = concurrency
        self._schedule_id = schedule_id
        self._interval = interval
        self._stop_event = threading.Event()
        # State mutabile condiviso tra thread loop e get_state_dict().
        # `lock_lost` viene mutato dal loop quando il lock e' perso e
        # non re-acquisibile. `execute()` legge questa stessa reference
        # via wrapper compat.
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
        """
        return self._state

    @property
    def lock_lost(self) -> bool:
        """True se il thread loop ha rilevato perdita del lock e
        re-acquire fallito. Read-only, mutato dal solo thread loop."""
        return bool(self._state.get("lock_lost", False))

    def __enter__(self) -> "LockHeartbeat":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Internal loop — copiato 1:1 da execution_manager._start_lock_heartbeat
    # (line 152-200 pre-refactor). Niente behavior change.
    # ------------------------------------------------------------------

    def _heartbeat_loop(self) -> None:
        # Usa stop_event.wait(timeout) invece di time.sleep: il
        # wait ritorna immediatamente True se stop_event e' settato,
        # cosi' lo stop e' deterministico e non aspettiamo l'intero
        # interval prima di fermarci.
        while not self._stop_event.wait(self._interval):
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
