"""Contract tests for the runtime DB initialization boundary."""

from __future__ import annotations

import os

import pytest


os.environ.setdefault(
    "DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test"
)

pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


def test_runtime_db_init_preserves_historical_order(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        "app.db.pg_connection.init_db",
        lambda: calls.append("pg_connection.init_db"),
    )
    monkeypatch.setattr(
        "app.core.intermediate.db.init_db",
        lambda: calls.append("intermediate.db.init_db"),
    )
    monkeypatch.setattr(
        "app.db.migrations_runner.apply_pending_migrations",
        lambda: calls.append("migrations_runner.apply_pending_migrations"),
    )

    import app.core.runtime.db_init as runtime_db_init

    runtime_db_init._original_init_all_dbs()

    assert calls == [
        "pg_connection.init_db",
        "intermediate.db.init_db",
        "migrations_runner.apply_pending_migrations",
    ]


def test_db_service_init_all_dbs_is_compatibility_wrapper(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        "app.core.runtime.db_init.init_all_dbs",
        lambda: calls.append("runtime.db_init.init_all_dbs"),
    )

    import app.db.db_service as db_service

    db_service._original_init_all_dbs()

    assert calls == ["runtime.db_init.init_all_dbs"]
