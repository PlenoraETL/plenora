"""Canonical database initialization boundary for application bootstrap."""

from __future__ import annotations


def init_all_dbs() -> None:
    """Initialize runtime database surfaces in the historical order."""
    from app.db.pg_connection import init_db as pg_init

    pg_init()

    import app.core.intermediate.db as intermediate_db

    intermediate_db.init_db()

    from app.db.migrations_runner import apply_pending_migrations

    apply_pending_migrations()

