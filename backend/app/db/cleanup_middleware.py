"""
Middleware FastAPI per il cleanup delle connessioni SQLite thread-local.

Problema risolto
================
`config_db_service.py` e `core/intermediate/db.py` mantengono le connessioni
SQLite in `threading.local()` per evitare di riaprirle ad ogni operazione
nello stesso thread. Funziona perfettamente nei task Celery (un thread per
task) ma in FastAPI (uvicorn con thread pool) i thread vengono **riusati**:

  - Request 1 → thread A → apre conn → salva in _local.conn → ritorna
  - Request 2 → thread A (riusato) → trova conn esistente → riusa
  - ...
  - Migliaia di request dopo: il file descriptor è vivo da ore, il WAL non
    fa checkpoint, e altre richieste possono andare in SQLITE_BUSY.

Soluzione
=========
Questo middleware chiude le connessioni del thread CORRENTE dopo aver servito
la request. Le chiamate `close_connection()` sono safe by design: lavorano
solo su `_local.conn` del thread chiamante (thread-local), quindi non
interferiscono con eventuali background task in altri thread.

Costo: ~1ms per request per riaprire la connessione (apertura SQLite locale
è leggera). Beneficio: nessun leak progressivo di file descriptor.

Ordine di esecuzione
====================
Il middleware DB cleanup va aggiunto DOPO il JWTMiddleware in `main.py`,
così:
    app.add_middleware(JWTMiddleware)
    app.add_middleware(DBConnectionCleanupMiddleware)
FastAPI esegue i middleware in ordine inverso (LIFO), quindi quello aggiunto
dopo è quello esterno (più vicino al client). Questo middleware deve essere
quello esterno: cleanup avviene per ULTIMO, dopo che tutto il request
processing è finito.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.utils.logger import get_logger

logger = get_logger(__name__)


class DBConnectionCleanupMiddleware(BaseHTTPMiddleware):
    """Chiude le connessioni SQLite thread-local dopo ogni request HTTP."""

    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            response = await call_next(request)
            return response
        finally:
            # Cleanup SEMPRE, anche su eccezione: vogliamo che il file
            # descriptor venga rilasciato pure se la request è fallita.
            self._close_connections()

    @staticmethod
    def _close_connections() -> None:
        """No-op per PostgreSQL: il connection pool gestisce il lifecycle.

        Con SQLite thread-local serviva chiudere esplicitamente.
        Con il pool psycopg2, le connessioni vengono restituite automaticamente
        dai wrapper fetchone/fetchall/execute in pg_connection.py.
        """
        pass
