"""UI conferma finale (06/11/2026): voci backend dell'elenco
docs/audits/ui-conferma-finale-2026-11-06.md. Un test per voce, che
fallirebbe senza la correzione:

- F3: migrazioni sotto UN advisory lock (preso prima di leggere le pending e
  di guardare gli indici INVALID; rilasciato anche su errore; attesa
  dichiarata). Su PostgreSQL vero: due istanze si mettono in fila;
- F4: CREATE INDEX CONCURRENTLY aspetta le transazioni al piu'
  MIGRATIONS_INDEX_WAIT_SECONDS; oltre, l'indice INVALID si toglie subito e
  l'avvio fallisce col motivo. Su PostgreSQL vero con una transazione aperta;
- F9: la query delle sorgenti Manipola scorre i nodi una volta (LATERAL),
  niente sottoquery correlate;
- G5: «ha un manifest» = manifest NON VUOTO, una regola in SQL e in Python
  (parita' su PostgreSQL vero), il dettaglio la espone;
- G7: il fuso dell'healthcheck viene dal beat (heartbeat), il ripiego e'
  dichiarato (`timezone_source`).
"""

from __future__ import annotations

import inspect
import re
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from tests2.shared import _real_pg
from tests2.shared._real_pg import real_pg_marks, real_pg_schema

pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _pg_error(sqlstate):
    """Un errore psycopg vero con quello sqlstate (55P03 = lock timeout)."""
    import psycopg

    assert sqlstate == "55P03"
    return psycopg.errors.LockNotAvailable("canceling statement due to lock timeout")


def _recording_conn(events, *, fail_on=None):
    conn = MagicMock()
    conn.autocommit = False
    cur = conn.cursor.return_value.__enter__.return_value

    def execute(stmt, params=()):
        text = str(stmt)
        events.append(("sql", text, params))
        if fail_on and fail_on[0] in text:
            raise _pg_error(fail_on[1])

    cur.execute.side_effect = execute
    cur.fetchone.return_value = {"invalid": 1}
    return conn


# ── F3 ─────────────────────────────────────────────────────────────────────

def test_migrations_take_the_advisory_lock_before_reading_pending(monkeypatch):
    from app.db import migrations_runner as mr

    events: list = []
    conn = _recording_conn(events)
    monkeypatch.setattr(mr, "ensure_schema", lambda: events.append(("ensure",)) or "plenora")
    monkeypatch.setattr(mr, "list_pending_migrations", lambda: events.append(("pending",)) or [])
    with patch("app.db.pg_connection.get_connection", return_value=conn), \
         patch("app.db.pg_connection.put_connection"):
        assert mr._REAL_APPLY_PENDING_MIGRATIONS() == []
    kinds = [e[0] if e[0] != "sql" else e[1] for e in events]
    lock = next(i for i, k in enumerate(kinds) if "pg_advisory_lock" in k)
    unlock = next(i for i, k in enumerate(kinds) if "pg_advisory_unlock" in k)
    assert lock < kinds.index(("ensure")) < kinds.index(("pending")) < unlock
    lock_sql = [e for e in events if e[0] == "sql" and "pg_advisory_lock" in e[1]][0]
    assert lock_sql[2] == (mr.MIGRATIONS_LOCK_KEY,)
    # Attesa del lock dichiarata (config.py DEFAULTS), poi tolta.
    timeouts = [e[2][0] for e in events if e[0] == "sql" and "lock_timeout" in e[1]]
    assert timeouts[0] == f"{Settings.DEFAULTS['MIGRATIONS_LOCK_WAIT_SECONDS'] * 1000}ms"
    assert timeouts[-1] == "0"


def test_lock_released_even_when_a_migration_fails(monkeypatch):
    from app.db import migrations_runner as mr

    events: list = []
    conn = _recording_conn(events)
    monkeypatch.setattr(mr, "_apply_pending_migrations_locked", MagicMock(side_effect=RuntimeError("boom")))
    with patch("app.db.pg_connection.get_connection", return_value=conn), \
         patch("app.db.pg_connection.put_connection") as put:
        with pytest.raises(RuntimeError, match="boom"):
            mr._REAL_APPLY_PENDING_MIGRATIONS()
    assert any("pg_advisory_unlock" in e[1] for e in events if e[0] == "sql")
    put.assert_called_once_with(conn)


def test_lock_wait_over_the_declared_time_fails_clearly(monkeypatch):
    from app.db import migrations_runner as mr

    events: list = []
    conn = _recording_conn(events, fail_on=("pg_advisory_lock", "55P03"))
    with patch("app.db.pg_connection.get_connection", return_value=conn), \
         patch("app.db.pg_connection.put_connection"):
        with pytest.raises(RuntimeError, match="MIGRATIONS_LOCK_WAIT_SECONDS"):
            mr._REAL_APPLY_PENDING_MIGRATIONS()
    assert not any("pg_advisory_unlock" in e[1] for e in events if e[0] == "sql")


def test_invalid_index_check_runs_only_inside_the_lock():
    """`_drop_if_invalid` e' chiamata solo dal giro fatto col lock in mano."""
    from app.db import migrations_runner as mr

    callers = [name for name, fn in inspect.getmembers(mr, inspect.isfunction)
               if fn.__module__ == mr.__name__ and "_drop_if_invalid(" in inspect.getsource(fn)
               and name != "_drop_if_invalid"]
    assert callers == ["_apply_no_transaction_migration"]
    src = inspect.getsource(mr._apply_pending_migrations_locked)
    assert "_apply_no_transaction_migration(" in src
    assert "with migrations_lock():" in inspect.getsource(mr._REAL_APPLY_PENDING_MIGRATIONS)


# ── F4 ─────────────────────────────────────────────────────────────────────

MIGRATION = "2026-11-06_log_runs_latest_by_project_index.sql"


def test_concurrent_index_wait_is_capped_and_the_invalid_leftover_is_dropped():
    from app.db import migrations_runner as mr

    sql = (mr.MIGRATIONS_DIR / MIGRATION).read_text(encoding="utf-8")
    events: list = []
    conn = _recording_conn(events, fail_on=("CREATE INDEX CONCURRENTLY", "55P03"))
    with pytest.raises(RuntimeError, match="MIGRATIONS_INDEX_WAIT_SECONDS"):
        mr._apply_no_transaction_migration(conn, sql, "plenora", MIGRATION)
    texts = [e[1] for e in events if e[0] == "sql"]
    create = next(i for i, t in enumerate(texts) if "CREATE INDEX CONCURRENTLY" in t)
    # Dopo il CREATE fallito: controllo e DROP dell'indice INVALID, niente tracking.
    assert any("DROP INDEX CONCURRENTLY" in t for t in texts[create + 1:])
    assert not any("schema_migrations" in t for t in texts)
    timeouts = [e[2][0] for e in events if e[0] == "sql" and "lock_timeout" in e[1]]
    assert timeouts == [f"{Settings.DEFAULTS['MIGRATIONS_INDEX_WAIT_SECONDS'] * 1000}ms", "0"]


def test_waits_are_declared_in_config_defaults():
    assert Settings.DEFAULTS["MIGRATIONS_LOCK_WAIT_SECONDS"] > 0
    assert Settings.DEFAULTS["MIGRATIONS_INDEX_WAIT_SECONDS"] > 0


def _real_pool(monkeypatch, search_path=None):
    from app.db import pg_connection

    monkeypatch.setattr(pg_connection, "get_connection", lambda: _real_pg.connect(autocommit=False, search_path=search_path))
    monkeypatch.setattr(pg_connection, "put_connection", lambda conn: conn.close())


@real_pg_marks
def test_two_instances_queue_on_the_lock_on_real_postgres(monkeypatch):
    from app.db import migrations_runner as mr

    _real_pool(monkeypatch)
    monkeypatch.setattr(mr, "_seconds_setting", lambda name: 30)
    marks: dict[str, float] = {}

    def first():
        with mr.migrations_lock():
            marks["a_in"] = time.monotonic()
            time.sleep(1.5)
            marks["a_out"] = time.monotonic()

    def second():
        time.sleep(0.3)
        with mr.migrations_lock():
            marks["b_in"] = time.monotonic()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert marks["b_in"] >= marks["a_out"]


@real_pg_marks
def test_lock_wait_timeout_on_real_postgres(monkeypatch):
    from app.db import migrations_runner as mr

    _real_pool(monkeypatch)
    holder = _real_pg.connect()
    holder.execute("SELECT pg_advisory_lock(%s)", (mr.MIGRATIONS_LOCK_KEY,))
    try:
        monkeypatch.setattr(mr, "_seconds_setting", lambda name: 1)
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="MIGRATIONS_LOCK_WAIT_SECONDS"):
            with mr.migrations_lock():
                pass
        assert time.monotonic() - started < 10
    finally:
        holder.close()


@real_pg_marks
def test_open_transaction_wait_is_capped_and_the_next_start_recovers_on_real_postgres(monkeypatch):
    """Una transazione aperta: l'attesa ha il tetto dichiarato e fallisce col
    motivo; il riavvio dopo (transazione chiusa) toglie l'INVALID e ricrea gli
    indici validi, senza cicli."""
    from psycopg import sql as pgsql

    from app.db import migrations_runner as mr

    sql = (mr.MIGRATIONS_DIR / MIGRATION).read_text(encoding="utf-8")
    monkeypatch.setattr(mr, "_seconds_setting", lambda name: 1)
    with real_pg_schema() as conn:
        schema = conn.execute("SELECT current_schema() AS s").fetchone()["s"]
        conn.execute("CREATE TABLE log_runs (id text, project_id text, group_id text, ts_start timestamptz)")
        conn.execute("CREATE TABLE schema_migrations (filename text primary key, applied_at timestamptz default now())")
        monkeypatch.setattr(mr, "_schema_migrations_table", lambda schema_name=None: pgsql.Identifier(schema, "schema_migrations"))
        blocker = _real_pg.connect(autocommit=False, search_path=schema)
        runner = _real_pg.connect(autocommit=False, search_path=schema)
        indexes = ("SELECT c.relname, i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                   "JOIN pg_namespace ns ON ns.oid = c.relnamespace WHERE ns.nspname = %s AND c.relname LIKE 'idx_log_runs%%' ORDER BY 1")
        try:
            blocker.execute("INSERT INTO log_runs VALUES ('r', 'p', 'g', now())")  # transazione aperta
            started = time.monotonic()
            with pytest.raises(RuntimeError, match="MIGRATIONS_INDEX_WAIT_SECONDS"):
                mr._apply_no_transaction_migration(runner, sql, "plenora", MIGRATION)
            assert time.monotonic() - started < 15
            assert conn.execute("SELECT count(*) AS n FROM schema_migrations").fetchone()["n"] == 0
            blocker.rollback()
            # Prossimo avvio: INVALID tolto e indici ricreati validi.
            mr._apply_no_transaction_migration(runner, sql, "plenora", MIGRATION)
            rows = conn.execute(indexes, (schema,)).fetchall()
        finally:
            blocker.rollback()
            blocker.close()
            runner.close()
    assert [(r["relname"], r["indisvalid"]) for r in rows] == [
        ("idx_log_runs_group_project_ts_start", True),
        ("idx_log_runs_project_ts_start", True),
    ]


# ── F9 ─────────────────────────────────────────────────────────────────────

def test_manipola_source_refs_scan_the_nodes_once():
    from app.core.projects import db as projects_db

    sql = inspect.getsource(projects_db.ProjectRepository.list_manipola_source_refs)
    assert "LEFT JOIN LATERAL" in sql and sql.count("FILTER (WHERE") == 2
    # Prima: CTE dei nodi riletta da due sottoquery correlate (N x M).
    assert not re.search(r"WHERE\s+n\.id\s*=\s*p\.id", sql)
    assert sql.count("jsonb_array_elements(") == 1


# ── G5 ─────────────────────────────────────────────────────────────────────

MANIFEST_CASES = [
    ({}, False),
    (None, False),
    ({"inputs": []}, True),
    ({"schema_version": 1, "inputs": [{"key": "input_file", "kind": "file"}]}, True),
]


@pytest.mark.parametrize("manifest, present", MANIFEST_CASES)
def test_manifest_present_is_a_non_empty_manifest(manifest, present):
    from app.core.eseguibili import db as eseguibili_db

    assert eseguibili_db.manifest_present(manifest) is present


def test_detail_says_no_manifest_for_an_empty_one():
    from app.core.eseguibili import router as eseguibili_router

    record = SimpleNamespace(
        canvas={"nodes": [], "edges": []}, manifest={}, group_id="grp-1", id="e1", name="E",
        description="", expected_columns=[], default_output_format="xlsx", default_output_name=None,
        icon=None, source_canvas_id=None, created_by="u", created_at="", updated_at="",
        project_type="arricchisci", active_version=1, active_version_id="v1",
    )
    detail = eseguibili_router._to_details(record)
    assert detail["has_manifest"] is False and detail["manifest"] is None


def test_every_manifest_presence_check_uses_the_rule():
    from app.core.eseguibili import router as eseguibili_router

    src = inspect.getsource(eseguibili_router)
    assert not re.search(r"if\s+(not\s+)?(record|raw)_?\.?manifest\s*:", src)
    assert "manifest_present(" in src


@real_pg_marks
def test_manifest_rule_is_the_same_in_sql_and_python_on_real_postgres():
    import json

    from app.core.eseguibili import db as eseguibili_db

    with real_pg_schema() as conn:
        conn.execute("CREATE TABLE eseguibile_versions (id text, manifest jsonb)")
        for i, (manifest, _present) in enumerate(MANIFEST_CASES):
            conn.execute("INSERT INTO eseguibile_versions VALUES (%s, %s)",
                         (f"v{i}", None if manifest is None else json.dumps(manifest)))
        rows = conn.execute(
            f"SELECT v.id, {eseguibili_db.MANIFEST_PRESENT_SQL} AS present FROM eseguibile_versions v ORDER BY v.id"
        ).fetchall()
        # Versione assente (LEFT JOIN senza riga): nessun manifest.
        missing = conn.execute(
            f"SELECT {eseguibili_db.MANIFEST_PRESENT_SQL} AS present "
            "FROM (SELECT 1) x LEFT JOIN eseguibile_versions v ON false"
        ).fetchone()["present"]
    assert [r["present"] for r in rows] == [p for _m, p in MANIFEST_CASES]
    assert missing is False


# ── G7 ─────────────────────────────────────────────────────────────────────

def test_healthcheck_timezone_comes_from_the_beat_heartbeat(monkeypatch):
    from datetime import datetime, timezone

    from app.core.scheduler import cron_schedule, healthcheck_service

    api_tz = str(cron_schedule.beat_timezone())
    monkeypatch.setattr(healthcheck_service.scheduler_db, "count_enabled_schedules", lambda: 0)
    monkeypatch.setattr(healthcheck_service.scheduler_db, "count_stuck_schedules", lambda limit: 0)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(healthcheck_service.scheduler_db, "get_beat_heartbeat",
                        lambda: {"ts": now, "timezone": "America/New_York"})
    hc = healthcheck_service.get_beat_healthcheck()
    assert (hc.timezone, hc.timezone_source) == ("America/New_York", "beat")
    # Heartbeat senza fuso (beat non aggiornato) o fuso non valido: ripiego dichiarato.
    for row in ({"ts": now, "timezone": None}, {"ts": now, "timezone": "Marte/Base"}, None):
        monkeypatch.setattr(healthcheck_service.scheduler_db, "get_beat_heartbeat", lambda row=row: row)
        hc = healthcheck_service.get_beat_healthcheck()
        assert (hc.timezone, hc.timezone_source) == (api_tz, "api_config")


def test_beat_writes_its_timezone_with_the_heartbeat(monkeypatch):
    from zoneinfo import ZoneInfo

    import workers.beat_scheduler as beat

    calls: list = []
    monkeypatch.setattr(beat, "_pg_execute", lambda sql, params: calls.append((sql, params)))
    monkeypatch.setattr(beat, "beat_timezone", lambda: ZoneInfo("Asia/Tokyo"))
    beat.PlenoraBeatScheduler._write_heartbeat(SimpleNamespace())
    sql, params = calls[0]
    assert "timezone" in sql and params[1] == "Asia/Tokyo"
