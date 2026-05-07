"""
Rate limiter thread-safe (token bucket in-memory) per il cluster

arricchisci/enrichment.
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
