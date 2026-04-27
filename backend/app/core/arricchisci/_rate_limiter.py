"""
Rate limiter thread-safe (token bucket in-memory) per il cluster
arricchisci/enrichment.

Origine
=======
Estratto da `app.core.arricchisci.enrichment_service` come parte di
Phase 5 Batch B3-A.1 (preflight ratificato in
`orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-A; pattern di estrazione
di utility class standalone, distinto dal mixin via MRO usato in B2
per le 6 famiglie di metodi `EnrichmentExecutor`).

Razionale del modulo separato
=============================
`RateLimiter` e' una classe utility:
  - Stateful interna (lock + last_call) ma NON dipende da
    `self.config` / `self.options` di `EnrichmentExecutor`.
  - Single responsibility: garantire che le chiamate API non superino
    il rate specificato (es. Nominatim 1 req/s).
  - Usata SOLO via `self._rate_limiter = RateLimiter(...)` in
    `EnrichmentExecutor.__init__` e via 4 callsite
    `self._rate_limiter.acquire()` (sync + async-via-to_thread).
  - **NON e' un mixin**: non viene ereditata, e' istanziata.

Quindi pattern di extraction:
  - File standalone `_rate_limiter.py` con la classe.
  - `enrichment_service.py` importa `from ._rate_limiter import
    RateLimiter` per preservare il riferimento simbolico.
  - L'instance attribute `self._rate_limiter` resta invariato in
    `EnrichmentExecutor.__init__`.

Behavior-preserving
===================
NESSUN cambio architetturale in B3-A.1:
  - Implementazione INVARIATA: token bucket in-memory thread-safe
    via `threading.Lock` + `time.time()` + `time.sleep(...)`.
  - NON Redis-distributed (eventuale Redis bucket e' carry-over per
    futura nuova Phase 0/5, fuori scope Phase 5).
  - Body bit-per-bit dalla `enrichment_service.py` pre-extraction.

Logger naming
=============
Modulo standalone — nessun emit di log in `RateLimiter` (verificato:
`acquire()` non logga, `__init__` non logga). Quindi non serve definire
un `logger` qui: nessun consumer ha mai catturato emit del
`RateLimiter`.

Coupling test contract
======================
Verificato pre-extraction (preflight B3-A):
  - 0 `monkeypatch.setattr(<*>_module, "logger", ...)` riguardanti
    `RateLimiter`.
  - 0 `inspect.getsource` su `RateLimiter` o sul modulo
    `enrichment_service`.
  - 0 `patch.object` su `RateLimiter` o `_rate_limiter`.
  - 0 caller esterni (importer di `RateLimiter` direttamente da
    `enrichment_service`): l'unico consumer e' l'instance attribute
    `EnrichmentExecutor._rate_limiter`.

Estrazione safe per costruzione. `enrichment_service.py` re-importa
`RateLimiter` per preservare retrocompat di eventuali consumer storici
non ancora identificati.

Il body sotto e' una copia BIT-PER-BIT dell'implementazione in
`enrichment_service.py` al momento di B3-A.1. Nessuna modifica di
logica, comment, ordine di operazione, sleep timing, lock semantics.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    """
    Rate limiter thread-safe con algoritmo token bucket.

    Garantisce che le chiamate API non superino il rate specificato.
    Usato sia dall'esecuzione sequenziale che parallela per rispettare
    i limiti delle API esterne (es. Nominatim: 1 req/s).
    """

    def __init__(self, rate: float):
        """
        Args:
            rate: Richieste al secondo (es. 5.0 = 5 req/s)
        """
        self.rate = rate
        self.interval = 1.0 / rate if rate > 0 else 0
        self.lock = threading.Lock()
        self.last_call = 0.0

    def acquire(self):
        """Attende se necessario per rispettare il rate limit."""
        if self.interval == 0:
            return

        with self.lock:
            now = time.time()
            time_since_last = now - self.last_call

            if time_since_last < self.interval:
                sleep_time = self.interval - time_since_last
                time.sleep(sleep_time)

            self.last_call = time.time()
