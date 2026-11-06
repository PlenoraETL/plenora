"""Indice unico sugli username normalizzati (lower(btrim(username))).

Regola: il controllo applicativo di ``auth.db.create_user`` (trim + casefold)
resta; il DB chiude la finestra di concorrenza con un indice unico e la sua
violazione ha lo stesso esito del controllo (``UsernameTakenError`` -> 409
``username_already_exists``). La migrazione che crea l'indice non fa fallire
l'installazione se esistono gia' doppioni: avvisa (log del backend e stato
sistema admin) e lascia l'indice assente.
"""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth import db as auth_db
from app.core.auth.exceptions import UsernameTakenError
from app.db import migrations_runner
from app.shared import error_codes
from tests2.helpers.builders import build_current_user


pytestmark = [pytest.mark.unit, pytest.mark.contract]

MIGRATION = "2026-11-05_users_username_normalized_unique.sql"


def _unique_violation(constraint: str) -> psycopg.errors.UniqueViolation:
    class _Violation(psycopg.errors.UniqueViolation):
        diag = SimpleNamespace(constraint_name=constraint)

    return _Violation(f"duplicate key value violates unique constraint \"{constraint}\"")


def _race(monkeypatch, constraint: str) -> list:
    """Il controllo applicativo non vede nessun utente (l'altro e' ancora in
    volo), l'INSERT trova il vincolo del DB."""
    calls: list = []
    monkeypatch.setattr(auth_db, "_fetchall", lambda sql, params=(): [])

    def execute(sql, params=()):
        calls.append(params)
        raise _unique_violation(constraint)

    monkeypatch.setattr(auth_db, "_execute", execute)
    return calls


# ------------------------------------------------- traduzione dell'errore


@pytest.mark.parametrize("constraint", sorted(auth_db.USERNAME_UNIQUE_CONSTRAINTS))
def test_username_constraint_violation_is_username_taken(constraint, monkeypatch):
    calls = _race(monkeypatch, constraint)
    with pytest.raises(UsernameTakenError):
        auth_db.create_user(user_id="x", username="Mario", password_hash="h", group_id="g")
    assert len(calls) == 1


def test_other_unique_violations_are_not_translated(monkeypatch):
    _race(monkeypatch, "users_pkey")
    with pytest.raises(psycopg.errors.UniqueViolation):
        auth_db.create_user(user_id="x", username="Mario", password_hash="h", group_id="g")


def test_concurrent_creation_answers_409_like_the_check(monkeypatch):
    from app.core.auth import users_router as module

    _race(monkeypatch, auth_db.USERNAME_NORMALIZED_INDEX)
    monkeypatch.setattr(module, "hash_password", lambda password: "hash")
    app = FastAPI()
    app.dependency_overrides[module.require_admin] = lambda: build_current_user(role="superadmin")
    app.include_router(module.users_router, prefix="/api")
    response = TestClient(app, raise_server_exceptions=False).post("/api/auth/users", json={
        "username": "Mario", "password": "x" * 12, "group_id": "g", "role": "user"})
    assert response.status_code == 409, response.text
    body = response.json()
    detail = body.get("detail", body)
    assert detail.get("error") == error_codes.USERNAME_ALREADY_EXISTS


# ------------------------------------------------- migrazione nel percorso di installazione


def _migration_sql() -> str:
    return (migrations_runner.MIGRATIONS_DIR / MIGRATION).read_text(encoding="utf-8")


def test_migration_is_in_the_install_path_and_runs_last():
    """Le migrazioni si applicano al boot del backend
    (``runtime.db_init.init_all_dbs`` -> ``apply_pending_migrations``), che
    install.sh / install-native.ps1 avviano: basta che il file sia nella
    cartella letta dal runner."""
    files = migrations_runner.list_migration_files()
    assert MIGRATION in files
    assert files.index(MIGRATION) > files.index("0000_bootstrap_base_tables.sql")


def test_migration_creates_the_index_only_without_collisions():
    sql = _migration_sql()
    body = re.sub(r"--[^\n]*", "", sql)
    assert body.strip().startswith("DO $$") and body.strip().endswith("END $$;")
    assert re.search(r"GROUP BY lower\(btrim\(username\)\)\s+HAVING count\(\*\) > 1", body)
    create = re.search(
        r"IF collisions IS NULL THEN\s+CREATE UNIQUE INDEX IF NOT EXISTS "
        + auth_db.USERNAME_NORMALIZED_INDEX
        + r"\s+ON plenora\.users \(lower\(btrim\(username\)\)\);\s+ELSE\s+RAISE WARNING",
        body,
    )
    assert create, "l'indice va creato solo nel ramo senza collisioni, altrimenti WARNING"
    # Nessuna istruzione fuori dal blocco DO che possa fallire sui doppioni.
    assert body.count("CREATE UNIQUE INDEX") == 1
    assert "RAISE EXCEPTION" not in body


def test_migration_warning_reaches_the_backend_log(caplog):
    handler = migrations_runner._migration_notice_logger(MIGRATION)
    with caplog.at_level(logging.INFO):
        handler(SimpleNamespace(severity_nonlocalized="WARNING",
                                message_primary="ux_users_username_normalized NON creato"))
    record = next(r for r in caplog.records if MIGRATION in r.getMessage())
    assert record.levelno == logging.WARNING
    assert "NON creato" in record.getMessage()


def test_runner_attaches_the_notice_handler_while_applying(monkeypatch, tmp_path):
    (tmp_path / "a.sql").write_text("SELECT 1;", encoding="utf-8")
    monkeypatch.setattr(migrations_runner, "MIGRATIONS_DIR", tmp_path)
    monkeypatch.setattr(migrations_runner, "ensure_schema", lambda: "plenora")
    monkeypatch.setattr(migrations_runner, "list_pending_migrations", lambda: ["a.sql"])
    events: list = []

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, *args, **kwargs):
            events.append("execute")

    class _Conn:
        autocommit = True

        def cursor(self):
            return _Cursor()

        def commit(self):
            pass

        def rollback(self):
            pass

        def add_notice_handler(self, cb):
            events.append("add")

        def remove_notice_handler(self, cb):
            events.append("remove")

    monkeypatch.setattr(migrations_runner.pg_connection_module, "get_connection", lambda: _Conn())
    monkeypatch.setattr(migrations_runner.pg_connection_module, "put_connection", lambda conn: None)
    # Il giro sotto il lock (il lock ha i suoi test: conferma finale F3).
    assert migrations_runner._apply_pending_migrations_locked() == ["a.sql"]
    first_add = events.index("add")
    assert events[first_add + 1] == "execute" and events[-1] == "remove"


# ------------------------------------------------- stato sistema


def _schema_block(monkeypatch, index_status):
    from app.core.admin_system_status import service

    monkeypatch.setattr(service, "fetch_schema_size_row",
                        lambda: {"size_pretty": "1 MB", "size_bytes": 1024 * 1024})
    monkeypatch.setattr(service, "fetch_critical_table_rows", lambda names: [])
    monkeypatch.setattr(service, "get_username_index_status", lambda: index_status)
    return service.get_db_schema_size().to_api_dict()


def test_system_status_reports_the_collisions(monkeypatch):
    block = _schema_block(monkeypatch, auth_db.UsernameIndexStatus(
        present=False, collisions=(("Mario", "mario"),)))
    assert block["status"] == "warn"
    assert "username" in block["message"]
    assert "Mario, mario" in block["warnings"][0]


def test_system_status_is_quiet_with_the_index(monkeypatch):
    block = _schema_block(monkeypatch, auth_db.UsernameIndexStatus(present=True))
    assert block["status"] == "ok"
    assert "warnings" not in block
