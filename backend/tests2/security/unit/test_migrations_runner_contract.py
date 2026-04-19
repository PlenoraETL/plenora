"""Contract test per migrations_runner (C8-nit).

Testa la logica di listing files + pending computation senza
toccare PG reale. Per la logica PG-side (CREATE schema_migrations,
INSERT tracking, rollback su fail) vedi l'integration test
test_migrations_runner_integration.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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
