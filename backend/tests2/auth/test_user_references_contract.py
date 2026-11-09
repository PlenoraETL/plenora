"""Eliminare un utente non perde e non riscrive lo storico (09/11/2026).

Caso reale: l'esecutore «E0071» ha lanciato un Eseguibile; DELETE
/auth/users/{id} rispondeva 500 («errore interno»): ``eseguibile_runs.user_id``
lo referenzia senza ON DELETE. Regola (``app.core.auth.user_references``):
prima di cancellare si contano i riferimenti della tabella dichiarativa
``USER_REFERENCES``; se ce ne sono, 409 ``user_has_history`` con i conteggi per
tipo e l'invito a disattivarlo; una ForeignKeyViolation residua e' comunque
409, mai 500. Il test di enumerazione confronta la tabella con le chiavi
esterne reali verso ``users`` di migrazioni e schema.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from fastapi import HTTPException

from app.core.auth import db as users_db
from app.core.auth import user_references
from app.core.auth.db import UserRow
from app.shared import error_codes

pytestmark = [pytest.mark.unit, pytest.mark.contract]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SQL_FILES = sorted((BACKEND_ROOT / "migrations").glob("*.sql")) + [BACKEND_ROOT / "app" / "db" / "config_schema.sql"]

_NAME = r'(?:"?\w+"?\.)?"?(\w+)"?'
_USERS = r'(?:"?\w+"?\.)?"?users"?\s*\(\s*"?id"?\s*\)'
_CREATE = re.compile(rf"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?{_NAME}\s*\((.*?)\n\s*\)\s*;", re.S | re.I)
_INLINE = re.compile(rf'^\s*"?(\w+)"?\s+[^\n]*?REFERENCES\s+{_USERS}([^\n]*)$', re.M | re.I)
_ALTER_FK = re.compile(
    rf"ALTER TABLE\s+(?:ONLY\s+)?{_NAME}\s+ADD CONSTRAINT\s+\"?\w+\"?\s+FOREIGN KEY\s*\(\s*\"?(\w+)\"?\s*\)\s*"
    rf"REFERENCES\s+{_USERS}([^;]*);", re.S | re.I)
_ALTER_COL = re.compile(
    rf"ALTER TABLE\s+(?:IF EXISTS\s+)?(?:ONLY\s+)?{_NAME}\s+ADD COLUMN\s+(?:IF NOT EXISTS\s+)?\"?(\w+)\"?"
    rf"[^;]*?REFERENCES\s+{_USERS}([^;,]*)", re.S | re.I)


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _real_user_foreign_keys() -> tuple[set[tuple[str, str]], dict[tuple[str, str], str]]:
    blocking: set[tuple[str, str]] = set()
    by_db: dict[tuple[str, str], str] = {}

    def add(table: str, column: str, tail: str) -> None:
        action = re.search(r"ON DELETE\s+(CASCADE|SET NULL|SET DEFAULT|RESTRICT|NO ACTION)", tail, re.I)
        key = (table.lower(), column.lower())
        if action and action.group(1).upper() in {"CASCADE", "SET NULL", "SET DEFAULT"}:
            by_db[key] = action.group(1).upper()
        else:
            blocking.add(key)

    for path in SQL_FILES:
        sql = _strip_comments(path.read_text(encoding="utf-8"))
        for table, body in _CREATE.findall(sql):
            for column, tail in _INLINE.findall(body):
                add(table, column, tail)
        for table, column, tail in _ALTER_FK.findall(sql):
            add(table, column, tail)
        for table, column, tail in _ALTER_COL.findall(sql):
            add(table, column, tail)
    return blocking, by_db


def test_enumeration_declared_references_match_the_real_foreign_keys():
    blocking, by_db = _real_user_foreign_keys()
    assert ("eseguibile_runs", "user_id") in blocking  # il caso reale
    assert blocking == {(ref.table, ref.column) for ref in user_references.USER_REFERENCES}
    assert by_db == dict(user_references.USER_REFERENCES_BY_DB)
    assert not blocking & set(by_db)


def test_count_is_one_query_over_the_whole_table():
    seen: dict = {}

    def fake_fetchall(sql, params):
        seen.update(sql=sql, params=params)
        return [{"ref": ref.key, "n": 1 if ref.key == "eseguibile_runs.user_id" else 0}
                for ref in user_references.USER_REFERENCES]

    out = user_references.count_user_references("u-e0071", fake_fetchall)
    assert seen["sql"].count("UNION ALL") == len(user_references.USER_REFERENCES) - 1
    assert seen["params"] == ("u-e0071",) * len(user_references.USER_REFERENCES)
    assert out == [{"table": "eseguibile_runs", "column": "user_id", "count": 1, "label": "1 esecuzione registrata"}]


def _admin():
    return SimpleNamespace(user_id="admin1", username="admin", group_id="g1", role="admin")


def _target():
    return UserRow(id="u-e0071", username="E0071", password_hash="$2b$12$hash", group_id="g1",
                   role="esecutore", is_active=True)


def _router(monkeypatch, *, references, delete=lambda uid: True):
    from app.core.auth import users_router as users_mod
    deleted: list[str] = []
    monkeypatch.setattr(users_mod._users_dal, "get_user", lambda uid: _target())
    monkeypatch.setattr(users_mod._users_dal, "count_user_references", lambda uid: references)

    def fake_delete(uid):
        deleted.append(uid)
        return delete(uid)

    monkeypatch.setattr(users_mod._users_dal, "delete_user", fake_delete)
    return users_mod, deleted


def test_real_case_user_with_an_execution_is_409_with_counts(monkeypatch):
    refs = [{"table": "eseguibile_runs", "column": "user_id", "count": 1, "label": "1 esecuzione registrata"}]
    users_mod, deleted = _router(monkeypatch, references=refs)
    with pytest.raises(HTTPException) as refused:
        users_mod.delete_user("u-e0071", _admin())
    assert refused.value.status_code == 409
    detail = refused.value.detail
    assert detail["error"] == error_codes.USER_HAS_HISTORY
    assert detail["message"] == (
        "L'utente «E0071» ha 1 esecuzione registrata: per non perdere lo storico non si può eliminare. Disattivalo.")
    assert detail["details"]["references"] == refs
    assert deleted == []


def test_several_kinds_are_listed_in_one_message():
    refs = [{"label": "3 esecuzioni registrate"}, {"label": "1 progetto creato"}, {"label": "2 ticket aperti"}]
    assert user_references.history_message("E0071", refs) == (
        "L'utente «E0071» ha 3 esecuzioni registrate, 1 progetto creato e 2 ticket aperti: "
        "per non perdere lo storico non si può eliminare. Disattivalo.")


def test_user_without_references_is_deleted_204(monkeypatch):
    users_mod, deleted = _router(monkeypatch, references=[])
    assert users_mod.delete_user("u-e0071", _admin()) is None
    assert deleted == ["u-e0071"]
    from app.core.auth.users_router import users_router
    route = next(r for r in users_router.routes if getattr(r, "path", "").endswith("/{user_id}") and "DELETE" in r.methods)
    assert route.status_code == 204


def test_residual_foreign_key_violation_is_409_never_500(monkeypatch):
    # Il DAL traduce la violazione...
    def boom(sql, params=()):
        raise psycopg.errors.ForeignKeyViolation("update or delete on table users violates foreign key")

    monkeypatch.setattr(users_db, "_execute", boom)
    with pytest.raises(user_references.UserHasHistoryError):
        users_db.delete_user("u-e0071")

    # ...e la route la rende 409 (riga scritta tra conteggio e cancellazione).
    def raise_history(uid):
        raise user_references.UserHasHistoryError("fk")

    users_mod, _ = _router(monkeypatch, references=[], delete=raise_history)
    with pytest.raises(HTTPException) as refused:
        users_mod.delete_user("u-e0071", _admin())
    assert refused.value.status_code == 409
    assert refused.value.detail["error"] == error_codes.USER_HAS_HISTORY
    assert "Disattivalo" in refused.value.detail["message"]
