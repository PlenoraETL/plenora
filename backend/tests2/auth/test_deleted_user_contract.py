"""Eliminare un utente mantenendo lo storico: la lapide (10/11/2026).

Caso reale: l'esecutore di prova «E0071» ha 1 riga in ``eseguibile_runs``;
DELETE /auth/users/{id} risponde 409 ``user_has_history``. Il superadmin ora
puo' eliminarlo mantenendo lo storico (POST
/auth/users/{id}/delete-keeping-history): la riga resta come lapide
(``deleted_at``, ``deleted_username``), ogni chiave esterna resta valida, lo
username originale torna libero, credenziali e MFA spariscono.

Regola (``app.shared.contracts.auth``): letture OPERATIVE solo con
``deleted_at IS NULL``; nome nello STORICO solo con ``user_display_name_sql``.
Il test di enumerazione analizza il sorgente del backend: ogni SQL che legge
o scrive ``users`` deve essere nella tabella dichiarativa
``app.core.auth.deleted_users.USERS_SQL_SITES`` e rispettarne il tipo.
"""

from __future__ import annotations

import ast
import re
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.auth import db as users_db
from app.core.auth import deleted_users
from app.shared.contracts import auth as auth_contract
from app.shared.contracts.auth import (
    NOT_DELETED_SQL,
    tombstone_username,
    user_display_name_sql,
)

pytestmark = [pytest.mark.unit, pytest.mark.contract]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCANNED_DIRS = ("app", "workers", "scripts")
# SQL che nomina la tabella users (anche con lo schema): FROM/JOIN/UPDATE/INTO.
_USERS_SQL = re.compile(r'\b(?:FROM|JOIN|UPDATE|INTO)\s+(?:"?plenora"?\.)?"?users"?(?![\w.])', re.I)


# ---------------------------------------------------------------- enumerazione


def _python_files():
    for folder in SCANNED_DIRS:
        root = BACKEND_ROOT / folder
        if root.exists():
            yield from sorted(root.rglob("*.py"))


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _blocks(tree: ast.Module, source: str) -> list[tuple[str, str, ast.AST]]:
    """Blocchi a cui si attribuisce un SQL: funzioni e metodi (qualname, anche
    annidati: conta la funzione piu' esterna) e assegnazioni di modulo."""
    blocks: list[tuple[str, str, ast.AST]] = []

    def walk(body, prefix: str) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                blocks.append((f"{prefix}{stmt.name}", ast.get_source_segment(source, stmt) or "", stmt))
            elif isinstance(stmt, ast.ClassDef):
                walk(stmt.body, f"{prefix}{stmt.name}.")
            elif isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                names = [t.id for t in targets if isinstance(t, ast.Name)]
                name = f"{prefix}{names[0]}" if names else f"{prefix}<assign>"
                blocks.append((name, ast.get_source_segment(source, stmt) or "", stmt))
            else:
                blocks.append((f"{prefix}<module>", ast.get_source_segment(source, stmt) or "", stmt))

    walk(tree.body, "")
    return blocks


def _real_users_sql_sites() -> dict[tuple[str, str], str]:
    """(modulo, nome) -> sorgente del blocco che contiene SQL su users."""
    sites: dict[tuple[str, str], str] = {}
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        if not _USERS_SQL.search(source):
            continue
        tree = ast.parse(source)
        skip = _docstring_nodes(tree)
        module = path.relative_to(BACKEND_ROOT).as_posix()
        for name, segment, block in _blocks(tree, source):
            if any(isinstance(node, ast.Constant) and isinstance(node.value, str)
                   and id(node) not in skip and _USERS_SQL.search(node.value)
                   for node in ast.walk(block)):
                sites[(module, name)] = sites.get((module, name), "") + segment
    return sites


def test_enumeration_every_sql_on_users_is_declared():
    real = _real_users_sql_sites()
    declared = {(site.module, site.name) for site in deleted_users.USERS_SQL_SITES}
    assert ("app/core/auth/db.py", "get_user") in real  # il punto di ogni autenticazione
    missing = sorted(set(real) - declared)
    stale = sorted(declared - set(real))
    assert not missing, f"SQL su users non dichiarati in USERS_SQL_SITES: {missing}"
    assert not stale, f"voci di USERS_SQL_SITES senza SQL su users: {stale}"


def test_enumeration_every_site_respects_its_kind():
    real = _real_users_sql_sites()
    wrong = []
    for site in deleted_users.USERS_SQL_SITES:
        source = real.get((site.module, site.name), "")
        assert site.reason.strip(), site
        if site.kind == "operativa" and "NOT_DELETED_SQL" not in source:
            wrong.append(f"{site.module}::{site.name} operativa senza NOT_DELETED_SQL")
        if site.kind == "storica_nome" and "user_display_name_sql(" not in source:
            wrong.append(f"{site.module}::{site.name} storica senza user_display_name_sql")
        if site.kind in {"storica_riga", "tutte_le_righe", "scrittura"} and "NOT_DELETED_SQL" in source:
            wrong.append(f"{site.module}::{site.name} dichiarato {site.kind} ma filtra le lapidi")
    assert not wrong, wrong


def test_enumeration_historical_row_reads_only_where_declared():
    callers = set()
    for path in _python_files():
        module = path.relative_to(BACKEND_ROOT).as_posix()
        if module == "app/core/auth/db.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "get_user_including_deleted":
                callers.add(module)
            if isinstance(node, ast.Name) and node.id == "get_user_including_deleted":
                callers.add(module)
    assert callers == set(deleted_users.HISTORICAL_ROW_READERS)


def test_the_regex_sees_every_shape_of_sql_on_users():
    for sql in ("SELECT * FROM users WHERE id = %s", "LEFT JOIN users u ON u.id = c.author_id",
                'UPDATE "plenora"."users" SET x = 1', "INSERT INTO users (id) VALUES (%s)",
                "DELETE FROM plenora.users WHERE group_id = %s"):
        assert _USERS_SQL.search(sql), sql
    for other in ("FROM user_mfa WHERE", "/content/users/{u}", "FROM users_backup", "users.username"):
        assert not _USERS_SQL.search(other), other


# ---------------------------------------------------------- regola del nome


def test_display_name_sql_says_deleted_user_with_the_original_name():
    sql = user_display_name_sql("u")
    assert f"u.{NOT_DELETED_SQL} THEN u.username" in sql
    assert "'utente eliminato (' || btrim(u.deleted_username) || ')'" in sql
    assert "THEN r.tested_by_name" in user_display_name_sql("u", "r.tested_by_name")
    with pytest.raises(ValueError):
        user_display_name_sql("u; DROP TABLE users")


def test_tombstone_username_is_unique_and_frees_the_original():
    assert tombstone_username("usr_abc") == "eliminato:usr_abc"
    assert "deleted_at" not in auth_contract.UserResponse.model_fields
    assert "deleted_username" not in auth_contract.UserResponse.model_fields


def test_migration_adds_nullable_columns_only():
    sql = (BACKEND_ROOT / "migrations" / "2026-11-10_users_deleted_tombstone.sql").read_text(encoding="utf-8")
    body = re.sub(r"--[^\n]*", "", sql)
    assert re.search(r"ALTER TABLE users\s+ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;", body)
    assert re.search(r"ALTER TABLE users\s+ADD COLUMN IF NOT EXISTS deleted_username TEXT;", body)
    assert not re.search(r"\b(UPDATE|DELETE|DROP|NOT NULL|DEFAULT)\b", body, re.I)


# ------------------------------------------------ DAL su un DB finto in memoria


_ROWS = [
    {"id": "usr_live", "username": "mario", "password_hash": "$h", "email": "m@x.it", "group_id": "g1",
     "role": "user", "is_active": True, "deleted_at": None, "deleted_username": None},
    {"id": "usr_e0071", "username": "eliminato:usr_e0071", "password_hash": "", "email": None, "group_id": "g1",
     "role": "esecutore", "is_active": False, "deleted_at": "2026-11-10T09:00:00+00:00", "deleted_username": "E0071"},
]


def _visible(sql: str, row: dict) -> bool:
    return NOT_DELETED_SQL not in sql or row["deleted_at"] is None


@pytest.fixture
def fake_users(monkeypatch):
    def fetchone(sql, params=()):
        key = "username" if "WHERE username" in sql else "id"
        return next((dict(r) for r in _ROWS if r[key] == params[0] and _visible(sql, r)), None)

    def fetchall(sql, params=()):
        return [dict(r) for r in _ROWS if _visible(sql, r)]

    monkeypatch.setattr(users_db, "_fetchone", fetchone)
    monkeypatch.setattr(users_db, "_fetchall", fetchall)


def test_operational_reads_never_see_a_tombstone(fake_users):
    assert users_db.get_user("usr_e0071") is None
    assert users_db.get_user("usr_live").username == "mario"
    assert users_db.get_user_by_username("eliminato:usr_e0071") is None
    assert [u.id for u in users_db.list_all_users()] == ["usr_live"]
    assert [u.id for u in users_db.list_users("g1")] == ["usr_live"]


def test_historical_row_read_sees_the_tombstone(fake_users):
    row = users_db.get_user_including_deleted("usr_e0071")
    assert row.deleted_username == "E0071" and row.role == "esecutore" and row.deleted_at


def test_original_username_is_free_again(fake_users):
    assert users_db.find_users_by_normalized_username("E0071") == []
    users_db.assert_username_writable("E0071")
    assert [u.id for u in users_db.find_users_by_normalized_username("eliminato:usr_e0071")] == ["usr_e0071"]


class _FakeSession:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount
        self.statements: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        return SimpleNamespace(rowcount=self.rowcount if sql.startswith("UPDATE users") else 1)


def _fake_uow(monkeypatch, session):
    opened = []

    @contextmanager
    def uow():
        opened.append(1)
        yield session

    monkeypatch.setattr(users_db, "unit_of_work", uow)
    return opened


def test_tombstone_is_one_transaction_that_clears_credentials_and_mfa(monkeypatch):
    session = _FakeSession(rowcount=1)
    opened = _fake_uow(monkeypatch, session)
    assert users_db.tombstone_user("usr_e0071") is True
    assert len(opened) == 1  # una transazione
    update_sql, params = session.statements[0]
    for fragment in ("deleted_at = %s", "deleted_username = username", "username = %s", "password_hash = ''",
                     "email = NULL", "is_active = FALSE", NOT_DELETED_SQL):
        assert fragment in update_sql
    assert params[1] == "eliminato:usr_e0071" and params[3] == "usr_e0071"
    assert [s for s, _ in session.statements[1:]] == list(users_db.TOMBSTONE_CLEANUP_SQL)
    # Le tabelle con ON DELETE CASCADE verso users sono tutte ripulite a mano.
    from app.core.auth import user_references
    cascade = {table for (table, _), action in user_references.USER_REFERENCES_BY_DB.items() if action == "CASCADE"}
    assert cascade == {re.search(r"DELETE FROM (\w+)", sql).group(1) for sql in users_db.TOMBSTONE_CLEANUP_SQL}


def test_tombstone_of_missing_or_already_deleted_user_changes_nothing(monkeypatch):
    session = _FakeSession(rowcount=0)
    _fake_uow(monkeypatch, session)
    assert users_db.tombstone_user("usr_e0071") is False
    assert len(session.statements) == 1


# ------------------------------------------------------------- autenticazione


def test_a_tombstone_cannot_log_in_nor_use_an_old_token(fake_users, monkeypatch):
    from fastapi.security import HTTPAuthorizationCredentials

    from app.core.auth import dependencies

    monkeypatch.setattr(dependencies, "decode_token", lambda token: {"sub": "usr_e0071", "jti": None})
    monkeypatch.setattr(dependencies, "is_jti_blacklisted", lambda jti: False)
    with pytest.raises(HTTPException) as refused:
        dependencies.get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials="t"))
    assert refused.value.status_code == 401

    from app.core.auth import async_user_recheck
    monkeypatch.setattr(async_user_recheck, "get_user", users_db.get_user)
    assert async_user_recheck.load_live_user("usr_e0071") is None
    revoked = async_user_recheck.recheck_eseguibile_authorized(
        "usr_e0071", "ese1", eseguibile_loader=lambda _id: {"id": "ese1"})
    assert revoked["status"] == "permission_revoked"


def test_login_with_the_original_username_finds_nobody(fake_users):
    # Il login legge get_user_by_username: la lapide ha un altro username e
    # comunque la lettura e' operativa.
    assert users_db.get_user_by_username("E0071") is None


def test_ownership_keeps_the_protection_of_a_deleted_superadmin(monkeypatch):
    from app.core.auth import ownership

    monkeypatch.setattr(ownership._users_dal, "get_user_including_deleted",
                        lambda uid: SimpleNamespace(role="superadmin", deleted_at="2026-11-10"))
    admin = SimpleNamespace(user_id="adm", role="admin", group_id="g1")
    with pytest.raises(HTTPException) as refused:
        ownership.assert_can_modify({"group_id": "g1", "created_by": "usr_sa_deleted"}, admin, "Connessione")
    assert refused.value.status_code == 403


# ----------------------------------------------------------------- endpoint


def _superadmin():
    return SimpleNamespace(user_id="sa1", username="root", group_id="g0", role="superadmin")


def _router(monkeypatch, *, exists=True, tombstoned=True):
    from app.core.auth import users_router as users_mod
    calls: list[str] = []
    monkeypatch.setattr(users_mod._users_dal, "get_user",
                        lambda uid: users_db.UserRow(id=uid, username="E0071") if exists else None)

    def tombstone(uid):
        calls.append(uid)
        return tombstoned

    monkeypatch.setattr(users_mod._users_dal, "tombstone_user", tombstone)
    return users_mod, calls


def test_superadmin_deletes_keeping_history_204(monkeypatch):
    users_mod, calls = _router(monkeypatch)
    assert users_mod.delete_user_keeping_history("usr_e0071", _superadmin()) is None
    assert calls == ["usr_e0071"]
    route = next(r for r in users_mod.users_router.routes
                 if getattr(r, "path", "").endswith("/{user_id}/delete-keeping-history"))
    assert route.methods == {"POST"} and route.status_code == 204


def test_only_superadmin_and_never_oneself(monkeypatch):
    users_mod, calls = _router(monkeypatch)
    admin = SimpleNamespace(user_id="adm", username="adm", group_id="g1", role="admin")
    with pytest.raises(HTTPException) as refused:
        users_mod.require_superadmin(admin)
    assert refused.value.status_code == 403
    with pytest.raises(HTTPException) as self_refused:
        users_mod.delete_user_keeping_history("sa1", _superadmin())
    assert self_refused.value.status_code == 400
    assert calls == []


@pytest.mark.parametrize("exists,tombstoned", [(False, True), (True, False)])
def test_missing_or_already_deleted_user_is_404(monkeypatch, exists, tombstoned):
    users_mod, _ = _router(monkeypatch, exists=exists, tombstoned=tombstoned)
    with pytest.raises(HTTPException) as refused:
        users_mod.delete_user_keeping_history("usr_e0071", _superadmin())
    assert refused.value.status_code == 404


# ------------------------------------------------------------ selettori


def test_ticket_cannot_be_assigned_to_a_deleted_user(monkeypatch):
    from app.core.tickets import service

    monkeypatch.setattr(service.tdb, "assignable_user_exists", lambda uid: uid == "usr_live")
    service._require_assignable(None)
    service._require_assignable("usr_live")
    with pytest.raises(HTTPException) as refused:
        service._require_assignable("usr_e0071")
    assert refused.value.status_code == 400
