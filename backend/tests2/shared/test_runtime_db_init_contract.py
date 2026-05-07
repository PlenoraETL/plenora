"""Contract tests for the runtime DB initialization boundary."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


os.environ.setdefault(
    "DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test"
)

pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


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


def test_db_service_init_all_dbs_wrapper_points_at_runtime_boundary():
    source = (_BACKEND_ROOT / "app" / "db" / "db_service.py").read_text(
        encoding="utf-8"
    )

    assert (
        "from app.core.runtime.db_init import init_all_dbs as runtime_init_all_dbs"
        in source
    )
    assert "runtime_init_all_dbs()" in source
