from __future__ import annotations

import importlib
import sys

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


def test_pg_connection_import_is_passive_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    sys.modules.pop("app.db.pg_connection", None)

    pg = importlib.import_module("app.db.pg_connection")

    assert pg._DATABASE_URL is None
    assert pg._POOL_CONFIG_LOADED is False
    with pytest.raises(RuntimeError, match="DATABASE_URL non configurato"):
        pg._get_pool()


def test_pg_connection_import_is_passive_with_malformed_pool_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("DB_POOL_MIN", "not-an-int")
    sys.modules.pop("app.db.pg_connection", None)

    pg = importlib.import_module("app.db.pg_connection")

    assert pg._POOL_CONFIG_LOADED is False
    with pytest.raises(RuntimeError, match="DB_POOL_MIN"):
        pg._get_pool()


def test_pg_connection_rejects_legacy_question_mark_placeholders():
    import app.db.pg_connection as pg

    with pytest.raises(ValueError, match="native psycopg2 placeholders"):
        pg._adapt_params("SELECT * FROM users WHERE id = ?", ("u1",))


def test_pg_connection_keeps_native_psycopg2_placeholders_unchanged():
    import app.db.pg_connection as pg

    sql, params = pg._adapt_params(
        "SELECT * FROM users WHERE id = %s AND group_id = %s",
        ("u1", "g1"),
    )

    assert sql == "SELECT * FROM users WHERE id = %s AND group_id = %s"
    assert params == ("u1", "g1")
