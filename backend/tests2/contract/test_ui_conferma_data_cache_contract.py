"""Conferma UI-1..UI-4 (06/11/2026), area «dati, cache, poller, liste server».

Elenco: docs/audits/ui-conferma-2026-11-06.md. Un test per difetto, che
fallirebbe senza la correzione:

- A-K1: la query mirata delle sorgenti Manipola usa funzioni che esistono
  su una colonna `canvas` jsonb (migration) E text (schema storico):
  verificata su PostgreSQL VERO quando c'e' (``PLENORA_REAL_PG_DSN``,
  marcatore ``integration``), sempre sulla firma SQL;
- A-K2 (lato server): i task di Carica dichiarano la connessione di
  destinazione, cosi' il client invalida il suo catalogo tabelle;
- A-K10: la lista dei file a cursore (next_cursor dalle righe del DB, errore
  che propaga, nessun file saltato o ripetuto da un caricamento in mezzo);
- A-K11: la migrazione degli indici di log_runs gira fuori transazione con
  CREATE INDEX CONCURRENTLY (e un indice INVALID si ricrea);
- C-K10: i conteggi della dashboard indipendenti e senza can_edit per riga.
"""

from __future__ import annotations

import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.auth import require_not_esecutore
from app.shared.contracts.auth import CurrentUser

pytestmark = [pytest.mark.unit, pytest.mark.contract]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REAL_PG_DSN = os.environ.get("PLENORA_REAL_PG_DSN", "")
needs_real_pg = pytest.mark.skipif(not REAL_PG_DSN, reason="PLENORA_REAL_PG_DSN non impostato: nessun PostgreSQL vero")


@pytest.fixture
def current_user() -> CurrentUser:
    return CurrentUser(user_id="u-1", username="tester", group_id="grp-1", role="admin")


@contextmanager
def real_pg_schema():
    """Uno schema temporaneo su un PostgreSQL vero, rimosso alla fine."""
    import psycopg
    from psycopg.rows import dict_row

    schema = f"conferma_{uuid.uuid4().hex[:10]}"
    conn = psycopg.connect(REAL_PG_DSN, autocommit=True, row_factory=dict_row)
    try:
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(f"SET search_path TO {schema}")
        yield conn
    finally:
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.close()


def _fetchall_on(conn):
    def fetchall(sql, params=()):
        return list(conn.execute(sql, params).fetchall())
    return fetchall


# ── A-K1 ────────────────────────────────────────────────────────────────────

def _refs_sql() -> str:
    import inspect

    from app.core.projects import db as projects_db

    return inspect.getsource(projects_db.ProjectRepository.list_manipola_source_refs)


def test_refs_query_never_calls_pg_input_is_valid_on_jsonb():
    """pg_input_is_valid(text, text): il primo argomento e' SEMPRE testo."""
    sql = _refs_sql()
    calls = re.findall(r"pg_input_is_valid\(([^,]+),\s*'jsonb'\)", sql)
    assert calls, "la query deve ancora segnare i canvas non leggibili"
    assert all(arg.strip().endswith("::text") for arg in calls), calls
    # Il documento si costruisce dal testo (vale per jsonb e per text).
    assert "canvas::text::jsonb" in sql
    assert not re.search(r"\bcanvas::jsonb\b", sql)


@needs_real_pg
@pytest.mark.integration
@pytest.mark.parametrize("column_type", ["jsonb", "text"])
def test_manipola_source_refs_on_real_postgres(monkeypatch, column_type):
    from app.core.projects import db as projects_db

    with real_pg_schema() as conn:
        conn.execute(
            f"CREATE TABLE projects (id text primary key, project_type text, canvas {column_type})"
        )
        good = (
            '{"nodes": [{"type": "project_source", "data": {"source_project_id": "s1"}},'
            ' {"type": "project_source", "source_project_id": "s2"},'
            ' {"type": "file_source", "file_source_id": "f1"},'
            ' {"type": "filter"}]}'
        )
        rows = [("m1", "manipola", good), ("m2", "manipola", '{"edges": []}'), ("e1", "estrai", good)]
        if column_type == "text":
            rows.append(("m3", "manipola", "{non e' json"))
        for row in rows:
            conn.execute("INSERT INTO projects VALUES (%s, %s, %s)", row)
        monkeypatch.setattr(projects_db, "_fetchall", _fetchall_on(conn))
        ids = {r[0] for r in rows}
        refs = {r.project_id: r for r in projects_db.ProjectRepository().list_manipola_source_refs(ids)}

    assert set(refs) == ({"m1", "m2", "m3"} if column_type == "text" else {"m1", "m2"})
    assert sorted(refs["m1"].source_project_ids) == ["s1", "s2"]
    assert refs["m1"].file_source_ids == ("f1",)
    assert refs["m1"].corrupted is False
    assert refs["m2"].source_project_ids == () and refs["m2"].corrupted is False
    if column_type == "text":
        assert refs["m3"].corrupted is True


# ── A-K2 (server): connessione di destinazione dei task di Carica ──────────

def test_carica_tasks_declare_destination_connection():
    from app.core.orchestrator import task_service as ts_mod

    service = ts_mod.TaskService.__new__(ts_mod.TaskService)
    service.create_carica_task = MagicMock(return_value=SimpleNamespace(task_id="ct-1"))
    node = {"id": "carica-1", "type": "carica", "data": {"connection_id": "conn-9", "dest_type": "db"}}
    with patch.object(ts_mod.carica_dispatcher, "find_connected_carica_nodes", return_value=[node]), \
         patch.object(ts_mod.carica_dispatcher, "build_carica_node_data", return_value={"id": "carica-1", "connection_id": "conn-9", "dest_type": "db"}), \
         patch.object(ts_mod, "CaricaExecutionContract") as contract:
        contract.return_value.to_payload.return_value = {}
        out = service.launch_carica_tasks_for_canvas(
            project_id="p1", canvas={"nodes": [node], "edges": []}, project_type="manipola",
            group_id="g1", output_format="csv", triggered_by="manual", user_id="u1",
            output_contract_loader=lambda _pid: None,
        )
    assert out["tasks"] == [{"task_id": "ct-1", "node_id": "carica-1", "connection_id": "conn-9"}]


# ── A-K10: lista dei file a cursore ────────────────────────────────────────

class _List:
    def __init__(self, files):
        self.files = files


def test_file_list_next_cursor_comes_from_db_rows_not_from_returned_files():
    from app.core.files.source_metadata import decode_file_cursor, list_file_sources

    t = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)
    rows = [{"id": "a", "created_at": t}, {"id": "senza-metadati", "created_at": t}, {"id": "c", "created_at": t}]
    out = list_file_sources(
        None, limit=3,
        db_list_file_sources=MagicMock(return_value=rows),
        get_file_source_by_id=lambda fid: None if fid == "senza-metadati" else fid,
        file_source_list_model=_List, use_app_models=True, logger=MagicMock(),
    )
    # Due file restituiti ma pagina PIENA nel DB: c'e' una pagina dopo.
    assert out.files == ["a", "c"]
    assert decode_file_cursor(out.next_cursor) == (t.isoformat(), "c")
    last = list_file_sources(
        None, limit=3,
        db_list_file_sources=MagicMock(return_value=rows[:1]),
        get_file_source_by_id=lambda fid: fid,
        file_source_list_model=_List, use_app_models=True, logger=MagicMock(),
    )
    assert last.next_cursor is None


def test_file_list_db_error_propagates_instead_of_empty_list():
    from app.core.files.source_metadata import list_file_sources

    with pytest.raises(RuntimeError):
        list_file_sources(
            None, limit=10,
            db_list_file_sources=MagicMock(side_effect=RuntimeError("db giu")),
            get_file_source_by_id=lambda fid: fid,
            file_source_list_model=_List, use_app_models=True, logger=MagicMock(),
        )


def test_files_route_passes_cursor_and_returns_next_cursor(monkeypatch, current_user):
    from app.core.files import router as files_router
    from app.core.files.source_metadata import encode_file_cursor

    seen = {}

    def fake_list(**kw):
        seen.update(kw)
        out = SimpleNamespace(files=[], next_cursor="dopo")
        return out

    monkeypatch.setattr(files_router.file_source_service, "list_file_sources", fake_list)
    monkeypatch.setattr(files_router, "visible_resource_ids", lambda *a, **k: {"x"})
    app = FastAPI()
    app.dependency_overrides[files_router.require_not_esecutore] = lambda: current_user
    app.include_router(files_router.router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=False)
    cursor = encode_file_cursor("2026-11-06T10:00:00+00:00", "f9")
    body = client.get("/api/files", params={"limit": 5, "cursor": cursor}).json()
    assert body == {"files": [], "next_cursor": "dopo"}
    assert seen["after"] == ("2026-11-06T10:00:00+00:00", "f9")
    assert client.get("/api/files", params={"cursor": "non-valido"}).status_code == 422


@needs_real_pg
@pytest.mark.integration
def test_file_pages_on_real_postgres_neither_skip_nor_repeat_during_upload(monkeypatch):
    from app.core.files import db as files_db

    with real_pg_schema() as conn:
        conn.execute(
            "CREATE TABLE file_sources (id text primary key, group_id text, name text, original_filename text,"
            " format text, size_bytes bigint, created_at timestamptz, created_by text, is_temp boolean DEFAULT FALSE)"
        )
        base = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)
        for i in range(7):
            # Due file con lo stesso istante: l'ordine per id deve tenerli distinti.
            stamp = base + timedelta(minutes=i if i != 6 else 5)
            conn.execute("INSERT INTO file_sources (id, name, original_filename, format, created_at) VALUES (%s, %s, %s, 'csv', %s)", (f"f{i}", f"f{i}", f"f{i}.csv", stamp))
        monkeypatch.setattr(files_db, "_fetchall", _fetchall_on(conn))
        first = files_db.list_file_sources(None, limit=3)
        # Un caricamento tra una pagina e l'altra (piu' recente di tutti).
        conn.execute("INSERT INTO file_sources (id, name, original_filename, format, created_at) VALUES ('nuovo', 'n', 'n.csv', 'csv', %s)", (base + timedelta(hours=1),))
        seen = [r["id"] for r in first]
        after = (first[-1]["created_at"].isoformat(), first[-1]["id"])
        while True:
            page = files_db.list_file_sources(None, limit=3, after=after)
            seen.extend(r["id"] for r in page)
            if len(page) < 3:
                break
            after = (page[-1]["created_at"].isoformat(), page[-1]["id"])
    assert sorted(seen) == sorted(f"f{i}" for i in range(7))
    assert len(seen) == len(set(seen))


# ── A-K11: indici di log_runs senza bloccare le scritture ──────────────────

MIGRATION = BACKEND_ROOT / "migrations" / "2026-11-06_log_runs_latest_by_project_index.sql"


def test_log_runs_index_migration_is_concurrent_and_outside_transaction():
    from app.db import migrations_runner as mr

    sql = MIGRATION.read_text(encoding="utf-8")
    assert mr.is_no_transaction_migration(sql)
    statements = mr.no_transaction_statements(sql)
    assert len(statements) == 2
    assert all(s.startswith("CREATE INDEX CONCURRENTLY IF NOT EXISTS") for s in statements)
    with pytest.raises(RuntimeError):
        mr.no_transaction_statements(mr.NO_TRANSACTION_DIRECTIVE + "\nCREATE INDEX x ON t (a);")


def test_runner_applies_no_transaction_migration_in_autocommit(tmp_path, monkeypatch):
    from app.db import migrations_runner as mr

    monkeypatch.setenv("DB_SCHEMA", "plenora")
    (tmp_path / MIGRATION.name).write_text(MIGRATION.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(mr, "MIGRATIONS_DIR", tmp_path)
    executed: list[tuple[bool, str]] = []
    conn = MagicMock()
    conn.autocommit = False
    cur = conn.cursor.return_value.__enter__.return_value
    cur.execute.side_effect = lambda stmt, params=(): executed.append((conn.autocommit, str(stmt)))
    cur.fetchone.return_value = {"invalid": 1}
    with patch.object(mr, "ensure_schema", return_value="plenora"), \
         patch("app.db.pg_connection.get_connection", return_value=conn), \
         patch("app.db.pg_connection.put_connection"), \
         patch.object(mr, "list_applied_migrations", return_value=[]):
        assert mr._REAL_APPLY_PENDING_MIGRATIONS() == [MIGRATION.name]
    creates = [(auto, s) for auto, s in executed if "CREATE INDEX CONCURRENTLY" in s]
    assert len(creates) == 2 and all(auto for auto, _ in creates)
    # Indice INVALID (interruzione precedente): tolto prima di ricrearlo.
    assert sum("DROP INDEX CONCURRENTLY" in s for _, s in executed) == 2
    assert conn.autocommit is False


@needs_real_pg
@pytest.mark.integration
def test_log_runs_index_migration_on_real_postgres():
    from app.db import migrations_runner as mr

    sql = MIGRATION.read_text(encoding="utf-8")
    with real_pg_schema() as conn:
        conn.execute("CREATE TABLE log_runs (id text, project_id text, group_id text, ts_start timestamptz)")
        for stmt in mr.no_transaction_statements(sql):
            conn.execute(stmt)  # autocommit: CONCURRENTLY ammesso
        for stmt in mr.no_transaction_statements(sql):
            conn.execute(stmt)  # rieseguibile
        names = {r["indexname"] for r in conn.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'log_runs'").fetchall()}
        with pytest.raises(Exception):
            with conn.transaction():
                conn.execute(mr.no_transaction_statements(sql)[0])
    assert {"idx_log_runs_project_ts_start", "idx_log_runs_group_project_ts_start"} <= names


# ── C-K10: conteggi indipendenti, senza can_edit ───────────────────────────

def test_dashboard_counts_are_independent_and_skip_per_row_work(monkeypatch, current_user):
    from app.core.arricchisci import router as api_routes
    from app.core.datasources import router as ds_routes
    from app.core.scheduler import router as sched_routes
    from app.core.dashboard import router as dashboard_mod

    def broken(*_a, **_k):
        raise RuntimeError("servizio giu")

    monkeypatch.setattr(api_routes, "visible_api_connections", broken)
    monkeypatch.setattr(ds_routes, "visible_datasources", lambda _cu, **kw: SimpleNamespace(datasources=[object()] * 4))
    mk = lambda pid, en: SimpleNamespace(pipeline=SimpleNamespace(project_id=pid), schedule=SimpleNamespace(enabled=en))
    monkeypatch.setattr(sched_routes, "list_schedules", lambda **kw: SimpleNamespace(schedules=[mk("p1", True)]))
    # Nessun can_edit per riga nel conteggio.
    monkeypatch.setattr(api_routes, "compute_can_edit", broken)
    monkeypatch.setattr(ds_routes, "compute_can_edit", broken)
    app = FastAPI()
    app.dependency_overrides[dashboard_mod.require_not_esecutore] = lambda: current_user
    app.include_router(dashboard_mod.router, prefix="/api")
    body = TestClient(app).get("/api/dashboard/counts").json()
    assert body == {
        "api_connections": None,
        "datasources": 4,
        "schedules_active": 1,
        "schedules_by_project": {"p1": 1},
        "unavailable": ["api_connections"],
    }


def test_list_routes_and_counts_share_the_visibility_functions():
    import inspect

    from app.core.arricchisci import router as api_routes
    from app.core.datasources import router as ds_routes

    assert "visible_api_connections(" in inspect.getsource(api_routes.list_api_connections)
    assert "visible_datasources(" in inspect.getsource(_unwrap(ds_routes.list_datasources))
    for fn in (api_routes.visible_api_connections, ds_routes.visible_datasources):
        assert "can_edit" not in inspect.getsource(fn).split('"""')[-1]


def _unwrap(fn):
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn
