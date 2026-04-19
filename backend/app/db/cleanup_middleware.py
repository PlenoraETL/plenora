"""
Middleware FastAPI per il cleanup delle connessioni DB.

Con PostgreSQL (psycopg2 ThreadedConnectionPool), le connessioni vengono
restituite al pool automaticamente dai wrapper in pg_connection.py.
Il middleware è mantenuto come hook per eventuali cleanup futuri.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class DBConnectionCleanupMiddleware(BaseHTTPMiddleware):
    """Cleanup hook post-request. No-op con PostgreSQL connection pool."""

    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            response = await call_next(request)
            return response
        finally:
            self._close_connections()

    @staticmethod
    def _close_connections() -> None:
        """No-op: il pool psycopg2 gestisce il lifecycle delle connessioni."""
        pass
