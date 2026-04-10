"""
tests/logs/unit/test_logs_router_coverage.py
=============================================
Unit test per logs_router.py: get_logs (tutti i filtri),
get_log_stats, clear_logs (superadmin vs admin).

Usa un DB SQLite in-memory via tmp_path e TestClient FastAPI.
"""

import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

from app.core.auth.auth import get_current_user, require_admin, CurrentUser
from app.core.logs.logs_router import router, _row_to_dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_user(role="superadmin", group_id="default"):
    return CurrentUser(
        user_id="test-uid",
        username="testuser",
        group_id=group_id,
        role=role,
    )


def _make_client(user=None):
    app = FastAPI()
    u = user or _mock_user()
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[require_admin] = lambda: u
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _create_log_db(db_path: Path, rows=None):
    """Crea un log.db con schema e righe opzionali."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL DEFAULT '',
            level TEXT NOT NULL DEFAULT 'INFO',
            module TEXT NOT NULL DEFAULT '',
            op TEXT NOT NULL DEFAULT '',
            msg TEXT NOT NULL DEFAULT '',
            project_id TEXT NOT NULL DEFAULT '',
            run_id TEXT NOT NULL DEFAULT '',
            group_id TEXT NOT NULL DEFAULT '',
            details TEXT NOT NULL DEFAULT '',
            search TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL DEFAULT '',
            module TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            ts_start TEXT NOT NULL DEFAULT '',
            ts_end TEXT,
            group_id TEXT NOT NULL DEFAULT '',
            steps TEXT NOT NULL DEFAULT '[]',
            debug_payload TEXT
        )
    """)
    if rows:
        for r in rows:
            conn.execute(
                "INSERT INTO logs (ts, level, module, op, msg, project_id, run_id, group_id, details) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    r.get("ts", "2026-01-01T00:00:00"),
                    r.get("level", "INFO"),
                    r.get("module", "estrai"),
                    r.get("op", "execute"),
                    r.get("msg", "test message"),
                    r.get("project_id", ""),
                    r.get("run_id", ""),
                    r.get("group_id", ""),
                    r.get("details", ""),
                )
            )
    conn.commit()
    conn.close()


@pytest.fixture
def log_db(tmp_path):
    """Crea un log.db con dati di test e patcha LOG_DB_PATH."""
    db_path = tmp_path / "log.db"
    _create_log_db(db_path, rows=[
        {"ts": "2026-01-01T10:00:00", "level": "INFO",    "module": "estrai",    "op": "execute",  "msg": "Query OK",        "project_id": "proj-1", "group_id": "grp-a"},
        {"ts": "2026-01-01T11:00:00", "level": "WARNING", "module": "manipola",  "op": "transform","msg": "Slow query",      "project_id": "proj-1", "group_id": "grp-a"},
        {"ts": "2026-01-01T12:00:00", "level": "ERROR",   "module": "estrai",    "op": "execute",  "msg": "Connection lost", "project_id": "proj-2", "group_id": "grp-b"},
        {"ts": "2026-01-02T08:00:00", "level": "INFO",    "module": "arricchisci","op": "enrich",  "msg": "Enrichment done", "project_id": "proj-3", "group_id": ""},
        {"ts": "2026-01-02T09:00:00", "level": "ERROR",   "module": "estrai",    "op": "preview",  "msg": "Timeout",         "project_id": "proj-1", "group_id": "grp-a",
         "details": '{"elapsed": 30}'},
    ])
    with patch("app.core.logs.logs_router.LOG_DB_PATH", db_path):
        yield db_path


@pytest.fixture
def empty_db(tmp_path):
    """DB senza log.db — simula primo avvio."""
    fake_path = tmp_path / "nonexistent.db"
    with patch("app.core.logs.logs_router.LOG_DB_PATH", fake_path):
        yield fake_path


# =============================================================================
# _row_to_dict
# =============================================================================

class TestRowToDict:

    def test_deserializes_json_details(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (id INT, details TEXT, msg TEXT)")
        conn.execute("INSERT INTO t VALUES (1, '{\"a\":1}', 'hello')")
        row = conn.execute("SELECT * FROM t").fetchone()
        d = _row_to_dict(row)
        assert d["details"] == {"a": 1}
        assert d["msg"] == "hello"

    def test_removes_empty_values(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (id INT, details TEXT, msg TEXT)")
        conn.execute("INSERT INTO t VALUES (1, '', 'hello')")
        row = conn.execute("SELECT * FROM t").fetchone()
        d = _row_to_dict(row)
        assert "details" not in d
        assert d["msg"] == "hello"

    def test_invalid_json_details_kept_as_string(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (id INT, details TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'not json {')")
        row = conn.execute("SELECT * FROM t").fetchone()
        d = _row_to_dict(row)
        assert d["details"] == "not json {"


# =============================================================================
# GET /logs — senza filtri
# =============================================================================

class TestGetLogs:

    def test_empty_db_returns_empty(self, empty_db):
        client = _make_client()
        res = client.get("/logs")
        assert res.status_code == 200
        data = res.json()
        assert data["entries"] == []
        assert data["total"] == 0

    def test_returns_all_entries(self, log_db):
        client = _make_client()
        res = client.get("/logs")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 5
        assert len(data["entries"]) == 5

    def test_pagination_limit(self, log_db):
        client = _make_client()
        res = client.get("/logs?limit=2&offset=0")
        data = res.json()
        assert len(data["entries"]) == 2
        assert data["has_more"] is True

    def test_pagination_offset(self, log_db):
        client = _make_client()
        res = client.get("/logs?limit=100&offset=3")
        data = res.json()
        assert len(data["entries"]) == 2  # 5 totali, offset 3 -> 2 rimanenti

    def test_order_desc(self, log_db):
        client = _make_client()
        res = client.get("/logs")
        entries = res.json()["entries"]
        ids = [e["id"] for e in entries]
        assert ids == sorted(ids, reverse=True)


# =============================================================================
# GET /logs — filtri
# =============================================================================

class TestGetLogsFilters:

    def test_filter_by_level(self, log_db):
        client = _make_client()
        res = client.get("/logs?level=ERROR")
        data = res.json()
        assert data["total"] == 2
        for e in data["entries"]:
            assert e["level"] == "ERROR"

    def test_filter_by_multiple_levels(self, log_db):
        client = _make_client()
        res = client.get("/logs?level=INFO,WARNING")
        data = res.json()
        assert data["total"] == 3

    def test_filter_by_module(self, log_db):
        client = _make_client()
        res = client.get("/logs?module=estrai")
        data = res.json()
        assert data["total"] == 3
        for e in data["entries"]:
            assert e["module"] == "estrai"

    def test_filter_by_op(self, log_db):
        client = _make_client()
        res = client.get("/logs?op=execute")
        data = res.json()
        assert data["total"] == 2

    def test_filter_by_project_id(self, log_db):
        client = _make_client()
        res = client.get("/logs?project_id=proj-1")
        data = res.json()
        assert data["total"] == 3

    def test_filter_by_search(self, log_db):
        client = _make_client()
        res = client.get("/logs?search=Timeout")
        data = res.json()
        assert data["total"] == 1
        assert "Timeout" in data["entries"][0]["msg"]

    def test_filter_by_after(self, log_db):
        client = _make_client()
        res = client.get("/logs?after=2026-01-02T00:00:00")
        data = res.json()
        assert data["total"] == 2

    def test_filter_by_before(self, log_db):
        client = _make_client()
        res = client.get("/logs?before=2026-01-01T11:00:00")
        data = res.json()
        assert data["total"] == 2

    def test_filter_combined(self, log_db):
        client = _make_client()
        res = client.get("/logs?level=ERROR&module=estrai")
        data = res.json()
        assert data["total"] == 2

    def test_details_deserialized(self, log_db):
        client = _make_client()
        res = client.get("/logs?search=Timeout")
        entry = res.json()["entries"][0]
        assert isinstance(entry.get("details"), dict)
        assert entry["details"]["elapsed"] == 30


# =============================================================================
# GET /logs — group isolation (non-superadmin)
# =============================================================================

class TestGetLogsGroupIsolation:

    def test_admin_sees_own_group_only(self, log_db):
        user = _mock_user(role="admin", group_id="grp-a")
        client = _make_client(user=user)
        res = client.get("/logs")
        data = res.json()
        # grp-a has 3 rows + group_id="" has 1 row = 4
        assert data["total"] == 4

    def test_admin_cannot_see_other_group(self, log_db):
        user = _mock_user(role="admin", group_id="grp-a")
        client = _make_client(user=user)
        res = client.get("/logs")
        entries = res.json()["entries"]
        for e in entries:
            gid = e.get("group_id", "")
            assert gid in ("grp-a", ""), f"Unexpected group_id: {gid}"

    def test_superadmin_sees_all(self, log_db):
        client = _make_client()  # default superadmin
        res = client.get("/logs")
        assert res.json()["total"] == 5


# =============================================================================
# GET /logs/stats
# =============================================================================

class TestGetLogStats:

    def test_empty_db(self, empty_db):
        client = _make_client()
        res = client.get("/logs/stats")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 0
        assert data["by_level"] == {}

    def test_stats_totals(self, log_db):
        client = _make_client()
        res = client.get("/logs/stats")
        data = res.json()
        assert data["total"] == 5

    def test_stats_by_level(self, log_db):
        client = _make_client()
        res = client.get("/logs/stats")
        by_level = res.json()["by_level"]
        assert by_level.get("INFO") == 2
        assert by_level.get("ERROR") == 2
        assert by_level.get("WARNING") == 1

    def test_stats_by_module(self, log_db):
        client = _make_client()
        res = client.get("/logs/stats")
        by_module = res.json()["by_module"]
        assert by_module.get("estrai") == 3

    def test_stats_by_operation(self, log_db):
        client = _make_client()
        res = client.get("/logs/stats")
        by_op = res.json()["by_operation"]
        assert by_op.get("execute") == 2

    def test_stats_recent_errors(self, log_db):
        client = _make_client()
        res = client.get("/logs/stats")
        errors = res.json()["recent_errors"]
        assert len(errors) == 2
        for e in errors:
            assert e["level"] == "ERROR"

    def test_stats_with_after_filter(self, log_db):
        client = _make_client()
        res = client.get("/logs/stats?after=2026-01-02T00:00:00")
        data = res.json()
        assert data["total"] == 2


# =============================================================================
# POST /logs/clear
# =============================================================================

class TestClearLogs:

    def test_superadmin_clears_all(self, log_db):
        user = _mock_user(role="superadmin")
        client = _make_client(user=user)
        res = client.post("/logs/clear")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"
        # Verifica che i log siano vuoti
        res2 = client.get("/logs")
        assert res2.json()["total"] == 0

    def test_admin_clears_own_group(self, log_db):
        user = _mock_user(role="admin", group_id="grp-a")
        client = _make_client(user=user)
        res = client.post("/logs/clear")
        assert res.status_code == 200
        # Verifica: grp-b log ancora presenti (come superadmin per il check)
        su = _mock_user(role="superadmin")
        client2 = _make_client(user=su)
        res2 = client2.get("/logs")
        # grp-b aveva 1 row
        remaining = res2.json()["total"]
        assert remaining >= 1

    def test_clear_when_no_db(self, empty_db):
        client = _make_client()
        res = client.post("/logs/clear")
        assert res.status_code == 200

    def test_clear_response_message(self, log_db):
        client = _make_client()
        res = client.post("/logs/clear")
        assert "svuotati" in res.json()["message"].lower() or "log" in res.json()["message"].lower()
