"""
Contract test per migrations_runner (C8-nit).

Testa la logica di listing files + pending computation senza
"""

from __future__ import annotations

import os
# Garantisce DATABASE_URL prima di qualunque import che possa caricare
# `app.db.pg_connection` (raise RuntimeError al module-level se assente).
# Necessario per i test che fanno `patch("app.db.pg_connection....")`.
os.environ.setdefault(
    "DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test"
)

from unittest.mock import MagicMock, patch

import pytest

from app.db import migrations_runner as mr


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


def test_list_migration_files_returns_sorted_sql_only(tmp_path, monkeypatch):
    """Solo .sql, ordinati lessicograficamente (= cronologicamente
    dato il pattern YYYY-MM-DD_...)."""
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "2026-04-12_a.sql").write_text("-- a")
    (d / "2026-04-16_c.sql").write_text("-- c")
    (d / "2026-04-14_b.sql").write_text("-- b")
    (d / "README.md").write_text("not sql")
    (d / "backup.sql.bak").write_text("wrong ext")

    monkeypatch.setattr(mr, "MIGRATIONS_DIR", d)

    got = mr.list_migration_files()
    assert got == [
        "2026-04-12_a.sql",
        "2026-04-14_b.sql",
        "2026-04-16_c.sql",
    ]


def test_list_migration_files_missing_dir_returns_empty(tmp_path, monkeypatch):
    """Se la directory migrations/ non esiste, ritorna [] senza sollevare."""
    monkeypatch.setattr(mr, "MIGRATIONS_DIR", tmp_path / "nonexistent")
    assert mr.list_migration_files() == []


def test_list_pending_migrations_computes_diff(tmp_path, monkeypatch):
    """pending = files on disk - rows in schema_migrations."""
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "a.sql").write_text("-- a")
    (d / "b.sql").write_text("-- b")
    (d / "c.sql").write_text("-- c")
    monkeypatch.setattr(mr, "MIGRATIONS_DIR", d)

    with patch.object(mr, "list_applied_migrations", return_value=["a.sql"]):
        pending = mr.list_pending_migrations()
    assert pending == ["b.sql", "c.sql"]


def test_list_pending_migrations_all_applied(tmp_path, monkeypatch):
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "a.sql").write_text("-- a")
    (d / "b.sql").write_text("-- b")
    monkeypatch.setattr(mr, "MIGRATIONS_DIR", d)

    with patch.object(mr, "list_applied_migrations",
                      return_value=["a.sql", "b.sql"]):
        assert mr.list_pending_migrations() == []


def test_list_pending_migrations_ignores_orphan_rows(tmp_path, monkeypatch):
    """Righe in schema_migrations che non corrispondono a file su disco
    NON causano 'pending' (sono migration obsolete, probabilmente
    file rimossi dopo rollback logico — non stiamo cercando missing)."""
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "a.sql").write_text("-- a")
    monkeypatch.setattr(mr, "MIGRATIONS_DIR", d)

    with patch.object(mr, "list_applied_migrations",
                      return_value=["a.sql", "old_deleted.sql"]):
        # Ci aspettiamo [] — "a.sql" e' applicato, "old_deleted" non
        # e' un file quindi non pending.
        assert mr.list_pending_migrations() == []


# BE-GO-READY-INSTALLATION-002-SCHEMA-AUTOCREATE

def _make_fake_conn():
    """Fake psycopg2 connection: cur.execute calls vengono spy-ati."""
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def test_ensure_schema_runs_create_schema_if_not_exists(monkeypatch):
    """ensure_schema esegue CREATE SCHEMA IF NOT EXISTS sullo schema
    configurato, con identifier quoting via psycopg2.sql."""
    from psycopg2 import sql as _sql

    monkeypatch.setenv("DB_SCHEMA", "plenora")

    conn, cur = _make_fake_conn()

    with patch("app.db.pg_connection.get_connection", return_value=conn), \
         patch("app.db.pg_connection.put_connection") as put_mock:
        out = mr.ensure_schema()

    assert out == "plenora"
    # Verifica esattamente una execute, con uno statement Composed che
    # contiene CREATE SCHEMA IF NOT EXISTS e un Identifier per lo schema
    # (no string-format di identifier SQL).
    assert cur.execute.call_count == 1
    stmt = cur.execute.call_args.args[0]
    assert isinstance(stmt, _sql.Composed)
    parts = list(stmt)
    assert any(
        isinstance(p, _sql.SQL) and "CREATE SCHEMA IF NOT EXISTS" in p.string
        for p in parts
    )
    idents = [p for p in parts if isinstance(p, _sql.Identifier)]
    assert len(idents) == 1
    assert idents[0].strings == ("plenora",)
    conn.commit.assert_called_once()
    put_mock.assert_called_once_with(conn)


def test_ensure_schema_uses_db_schema_env(monkeypatch):
    """Il nome schema viene da DB_SCHEMA, non hardcoded."""
    from psycopg2 import sql as _sql

    monkeypatch.setenv("DB_SCHEMA", "tenant_x")

    conn, cur = _make_fake_conn()
    with patch("app.db.pg_connection.get_connection", return_value=conn), \
         patch("app.db.pg_connection.put_connection"):
        out = mr.ensure_schema()

    assert out == "tenant_x"
    stmt = cur.execute.call_args.args[0]
    idents = [p for p in stmt if isinstance(p, _sql.Identifier)]
    assert idents and idents[0].strings == ("tenant_x",)


def test_ensure_schema_rejects_invalid_identifier(monkeypatch):
    """Schema name fuori da [A-Za-z_][A-Za-z0-9_]* solleva RuntimeError
    senza aprire la connection."""
    monkeypatch.setenv("DB_SCHEMA", "bad name; DROP SCHEMA public")

    with patch("app.db.pg_connection.get_connection") as get_mock:
        with pytest.raises(RuntimeError, match="DB_SCHEMA"):
            mr.ensure_schema()
        get_mock.assert_not_called()


def test_ensure_schema_rollback_on_failure(monkeypatch):
    """Su execute fallita, rollback eseguito e connection restituita."""
    monkeypatch.setenv("DB_SCHEMA", "plenora")

    conn, cur = _make_fake_conn()
    cur.execute.side_effect = RuntimeError("boom")

    with patch("app.db.pg_connection.get_connection", return_value=conn), \
         patch("app.db.pg_connection.put_connection") as put_mock:
        with pytest.raises(RuntimeError, match="boom"):
            mr.ensure_schema()

    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()
    put_mock.assert_called_once_with(conn)


def test_apply_pending_migrations_creates_schema_before_tracking_table(
    tmp_path, monkeypatch
):
    """Contract: ensure_schema gira PRIMA di creare schema_migrations.
    Su DB vuoto, invertire questo ordine produce InvalidSchemaName.

    Nessun file di migration su disco -> il runner si ferma dopo
    ensure_schema + CREATE schema_migrations, senza eseguire migration
    SQL. Verifichiamo l'ordine osservato sulle chiamate spy."""
    monkeypatch.setenv("DB_SCHEMA", "plenora")
    monkeypatch.setattr(mr, "MIGRATIONS_DIR", tmp_path / "empty")

    calls: list[str] = []

    def fake_ensure_schema(schema=None):
        calls.append("ensure_schema")
        return schema or "plenora"

    conn, cur = _make_fake_conn()

    def fake_cursor_execute(stmt, params=()):
        calls.append("execute_tracking_table")

    monkeypatch.setattr(mr, "ensure_schema", fake_ensure_schema)
    cur.execute.side_effect = fake_cursor_execute
    with patch("app.db.pg_connection.get_connection", return_value=conn), \
         patch("app.db.pg_connection.put_connection"), \
         patch.object(mr, "list_applied_migrations", return_value=[]):
        applied = mr._REAL_APPLY_PENDING_MIGRATIONS()

    assert applied == []
    assert calls[0] == "ensure_schema", (
        f"ensure_schema deve essere la prima operazione, ordine osservato: {calls}"
    )
    assert calls[1:2] == ["execute_tracking_table"], (
        f"CREATE TABLE schema_migrations non eseguito subito dopo ensure_schema: {calls}"
    )


def test_apply_pending_migrations_tracking_table_uses_schema_env(
    tmp_path, monkeypatch
):
    """La tracking table deve usare lo schema risolto, non hardcoded
    `plenora.schema_migrations` hardcoded. Questo chiude davvero il path clean-room
    con `DB_SCHEMA` custom."""
    from psycopg2 import sql as _sql

    monkeypatch.setenv("DB_SCHEMA", "tenant_x")
    monkeypatch.setattr(mr, "MIGRATIONS_DIR", tmp_path / "empty")

    conn, cur = _make_fake_conn()

    with patch.object(mr, "ensure_schema", return_value="tenant_x"), \
         patch("app.db.pg_connection.get_connection", return_value=conn), \
         patch("app.db.pg_connection.put_connection"), \
         patch.object(mr, "list_applied_migrations", return_value=[]):
        applied = mr._REAL_APPLY_PENDING_MIGRATIONS()

    assert applied == []
    stmt = cur.execute.call_args.args[0]
    assert isinstance(stmt, _sql.Composed)
    identifiers = [p for p in stmt if isinstance(p, _sql.Identifier)]
    assert identifiers
    assert identifiers[0].strings == ("tenant_x", "schema_migrations")
