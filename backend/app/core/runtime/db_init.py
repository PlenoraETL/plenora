"""Canonical database initialization boundary for application bootstrap."""

from __future__ import annotations


def init_all_dbs() -> None:
    """Initialize runtime database surfaces in the historical order."""
    # Intentional bootstrap boundary imports: tests and startup guards patch
    # these module functions before calling init_all_dbs. Binding them at
    # module load would bypass the patch and can open a real DB connection.
    from app.db.pg_connection import init_db as pg_init

    pg_init()

    from app.core.intermediate.init import init_db as intermediate_init

    intermediate_init()

    from app.db.migrations_runner import apply_pending_migrations

    apply_pending_migrations()
