"""Semplificazione H1–H7 (06/11/2026), voci di avvio e migrazioni.

Elenco: docs/audits/ui-conferma-finale-2026-11-06.md, «Giro successivo, su
c916dc42». Un test per voce, che fallirebbe senza la correzione (le voci della
pulizia H1/H2/H4 sono in tests2/orchestrator/unit/test_full_cleanup_order_contract.py):

- H5: nessuna migrazione puo' aspettare all'avvio un CREATE INDEX
  CONCURRENTLY (il runner non ha piu' il percorso «fuori transazione»); gli
  indici facoltativi li crea il beat dopo l'avvio, con attese di pochi
  secondi e riprova al giro dopo; l'installer avvia worker e beat solo col
  backend pronto;
- H6: le istanze che partono insieme si mettono in fila su un advisory lock
  di TRANSAZIONE (si rilascia da solo: niente unlock che possa fallire e
  lasciare il lock su una connessione del pool); chi arriva secondo trova
  la migrazione registrata e la salta.
"""

from __future__ import annotations

import inspect
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests2.shared import _real_pg
from tests2.shared._real_pg import real_pg_marks, real_pg_schema

pytestmark = [pytest.mark.unit, pytest.mark.contract]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent


# ── H5: niente attese lunghe all'avvio ─────────────────────────────────────

def test_no_migration_builds_an_index_concurrently():
    """Enumerazione: ogni migrazione gira in transazione, veloce."""
    from app.db import migrations_runner as mr

    for name in mr.list_migration_files():
        code = re.sub(r"--[^\n]*", "", (mr.MIGRATIONS_DIR / name).read_text(encoding="utf-8"))
        assert not re.search(r"\bCONCURRENTLY\b", code, re.I), name
    # Il percorso «fuori transazione» e le sue attese non ci sono piu'.
    src = inspect.getsource(mr)
    for gone in ("no-transaction", "lock_timeout", "pg_advisory_unlock", "pg_advisory_lock("):
        assert gone not in src, gone


class _FakeIndexConn:
    """Connessione finta: stato degli indici e istruzioni eseguite."""

    def __init__(self, states, *, lock_fail=()):
        self.states = dict(states)
        self.lock_fail = set(lock_fail)
        self.executed: list[str] = []
        self.closed = False

    def execute(self, stmt, params=()):
        text = stmt.as_string(None) if hasattr(stmt, "as_string") else str(stmt)
        self.executed.append(text)
        if "pg_index" in text:
            state = self.states.get(params[0], "missing")
            row = None if state == "missing" else {"valid": state == "valid", "building": state == "building"}
            return SimpleNamespace(fetchone=lambda: row)
        if "CREATE INDEX" in text:
            name = next(n for n in self.states if f'"{n}"' in text)
            if name in self.lock_fail:
                import psycopg
                raise psycopg.errors.LockNotAvailable("canceling statement due to lock timeout")
        return SimpleNamespace(fetchone=lambda: None)

    def close(self):
        self.closed = True


def test_optional_indexes_create_what_is_missing_and_retry_on_a_busy_table():
    from app.db import optional_indexes as oi

    names = list(oi.OPTIONAL_INDEXES)
    conn = _FakeIndexConn({names[0]: "invalid", names[1]: "missing"}, lock_fail={names[1]})
    created = oi.ensure_optional_indexes(lambda: conn)
    assert created == [names[0]]  # il secondo: lock non ottenuto, al giro dopo
    assert conn.closed
    assert conn.executed[0] == f"SET lock_timeout = '{oi.LOCK_TIMEOUT}'"
    drop = next(i for i, t in enumerate(conn.executed) if "DROP INDEX CONCURRENTLY" in t)
    create = next(i for i, t in enumerate(conn.executed) if "CREATE INDEX CONCURRENTLY" in t)
    assert drop < create  # INVALID: si toglie, poi si ricrea


def test_optional_indexes_leave_valid_and_building_ones_alone():
    from app.db import optional_indexes as oi

    names = list(oi.OPTIONAL_INDEXES)
    conn = _FakeIndexConn({names[0]: "valid", names[1]: "building"})
    assert oi.ensure_optional_indexes(lambda: conn) == []
    assert not any("DROP" in t or "CREATE" in t for t in conn.executed)
    assert conn.closed


def test_the_beat_builds_optional_indexes_every_ten_minutes():
    import workers.beat_scheduler as beat

    calls: list = []
    sched = SimpleNamespace(_last_optional_indexes=0.0)
    with patch("app.db.optional_indexes.ensure_optional_indexes", side_effect=calls.append):
        beat.PlenoraBeatScheduler._ensure_optional_indexes(sched)
        beat.PlenoraBeatScheduler._ensure_optional_indexes(sched)  # entro 10 min: niente
    assert len(calls) == 1
    assert "self._ensure_optional_indexes()" in inspect.getsource(beat.PlenoraBeatScheduler.tick)


def test_installer_starts_workers_and_beat_only_with_the_backend_ready():
    text = (REPO_ROOT / "scripts" / "install-native.ps1").read_text(encoding="utf-8-sig")
    start = text.index("# Avvio in ordine: backend")
    block = text[start:text.index("Get-Service plenora-*", start)]
    backend = block.index("Start-Service 'plenora-backend'")
    ready = block.index("Wait-BackendReady")
    workers = block.index("foreach ($id in $workerServiceIds")
    assert backend < ready < workers
    assert "'plenora-backend') + $workerServiceIds" not in block


@real_pg_marks
def test_optional_indexes_never_wait_long_and_recover_on_real_postgres(monkeypatch):
    """Una transazione aperta su log_runs: il giro rinuncia in pochi secondi
    (nessun errore); chiusa la transazione, il giro dopo toglie l'INVALID e
    crea gli indici validi."""
    from app.db import optional_indexes as oi

    monkeypatch.setattr(oi, "LOCK_TIMEOUT", "1s")
    with real_pg_schema() as conn:
        schema = conn.execute("SELECT current_schema() AS s").fetchone()["s"]
        conn.execute("CREATE TABLE log_runs (id text, project_id text, group_id text, ts_start timestamptz)")
        connect = lambda: _real_pg.connect(search_path=schema)  # noqa: E731
        blocker = _real_pg.connect(autocommit=False, search_path=schema)
        try:
            blocker.execute("INSERT INTO log_runs VALUES ('r', 'p', 'g', now())")  # transazione aperta
            started = time.monotonic()
            first = oi.ensure_optional_indexes(connect)
            assert time.monotonic() - started < 15
            assert first == []
        finally:
            blocker.rollback()
            blocker.close()
        second = oi.ensure_optional_indexes(connect)
        rows = conn.execute(
            "SELECT c.relname, i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "JOIN pg_namespace ns ON ns.oid = c.relnamespace WHERE ns.nspname = %s "
            "AND c.relname LIKE 'idx_log_runs%%' ORDER BY 1", (schema,),
        ).fetchall()
    assert sorted(second) == sorted(oi.OPTIONAL_INDEXES)
    assert [(r["relname"], r["indisvalid"]) for r in rows] == [
        ("idx_log_runs_group_project_ts_start", True),
        ("idx_log_runs_project_ts_start", True),
    ]


# ── H6: lock di transazione, chi arriva secondo salta ──────────────────────

def _recording_conn(events, *, applied=False):
    conn = MagicMock()
    conn.autocommit = False
    cur = conn.cursor.return_value.__enter__.return_value
    cur.execute.side_effect = lambda stmt, params=(): events.append(str(stmt))
    cur.fetchone.return_value = {"done": 1} if applied else None
    conn.commit.side_effect = lambda: events.append("COMMIT")
    return conn


def _run_runner(tmp_path, monkeypatch, conn):
    from app.db import migrations_runner as mr

    (tmp_path / "a.sql").write_text("ALTER TABLE t ADD COLUMN IF NOT EXISTS c TEXT;", encoding="utf-8")
    monkeypatch.setattr(mr, "MIGRATIONS_DIR", tmp_path)
    monkeypatch.setattr(mr, "ensure_schema", lambda: "plenora")
    monkeypatch.setattr(mr, "list_applied_migrations", lambda: [])
    with patch("app.db.pg_connection.get_connection", return_value=conn), \
         patch("app.db.pg_connection.put_connection"):
        return mr._REAL_APPLY_PENDING_MIGRATIONS()


def test_each_migration_takes_the_transaction_lock_then_rechecks_tracking(tmp_path, monkeypatch):
    from app.db import migrations_runner as mr

    events: list[str] = []
    assert _run_runner(tmp_path, monkeypatch, _recording_conn(events)) == ["a.sql"]
    lock = events.index("SELECT pg_advisory_xact_lock(%s)")
    check = next(i for i, e in enumerate(events) if "SELECT 1 AS done" in e)
    migration = events.index("ALTER TABLE t ADD COLUMN IF NOT EXISTS c TEXT;")
    commit = max(i for i, e in enumerate(events) if e == "COMMIT")
    assert lock < check < migration < commit
    assert mr.MIGRATIONS_LOCK_KEY == 0x706C656E6F7261


def test_a_migration_applied_meanwhile_by_another_instance_is_skipped(tmp_path, monkeypatch):
    events: list[str] = []
    assert _run_runner(tmp_path, monkeypatch, _recording_conn(events, applied=True)) == []
    assert "ALTER TABLE t ADD COLUMN IF NOT EXISTS c TEXT;" not in events


@real_pg_marks
def test_two_instances_apply_a_migration_once_on_real_postgres(tmp_path, monkeypatch):
    from psycopg import sql as pgsql

    from app.db import migrations_runner as mr
    from app.db import pg_connection

    (tmp_path / "a.sql").write_text(
        "SELECT pg_sleep(1.5); INSERT INTO applied_log VALUES (1);", encoding="utf-8")
    monkeypatch.setattr(mr, "MIGRATIONS_DIR", tmp_path)
    with real_pg_schema() as conn:
        schema = conn.execute("SELECT current_schema() AS s").fetchone()["s"]
        conn.execute("CREATE TABLE applied_log (n int)")
        conn.execute("CREATE TABLE schema_migrations (filename text primary key, applied_at timestamptz default now())")
        monkeypatch.setattr(mr, "ensure_schema", lambda: schema)
        monkeypatch.setattr(mr, "_schema_name", lambda schema_name=None: schema)
        monkeypatch.setattr(mr, "_schema_migrations_table",
                            lambda schema_name=None: pgsql.Identifier(schema, "schema_migrations"))
        monkeypatch.setattr(pg_connection, "get_connection",
                            lambda: _real_pg.connect(autocommit=False, search_path=schema))
        monkeypatch.setattr(pg_connection, "put_connection", lambda c: c.close())
        results: list = []
        threads = [threading.Thread(target=lambda: results.append(mr._REAL_APPLY_PENDING_MIGRATIONS()))
                   for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        count = conn.execute("SELECT count(*) AS n FROM applied_log").fetchone()["n"]
        # Lock di transazione: finito il giro nessuno lo tiene piu'.
        free = conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (mr.MIGRATIONS_LOCK_KEY,)).fetchone()["ok"]
        conn.execute("SELECT pg_advisory_unlock(%s)", (mr.MIGRATIONS_LOCK_KEY,))
    assert sorted(results, key=len) == [[], ["a.sql"]]
    assert count == 1 and free is True
