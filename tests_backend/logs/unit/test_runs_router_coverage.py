"""
tests/logs/unit/test_runs_router_coverage.py
=============================================
Unit test per runs_router.py: get_runs (tutti i filtri),
get_run_detail (con log correlati), get_run_payload (deserializzazione JSON).

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

from app.core.auth.auth import get_current_user, CurrentUser
from app.core.logs.runs_router import router, _run_to_dict, _log_to_dict


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
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _create_db(db_path: Path, runs=None, logs=None):
    """Crea un log.db con tabelle runs e logs e dati opzionali."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL DEFAULT '',
            project_name TEXT NOT NULL DEFAULT '',
            module TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            ts_start TEXT NOT NULL DEFAULT '',
            ts_end TEXT,
            duration_s REAL,
            rows_out INTEGER,
            group_id TEXT NOT NULL DEFAULT '',
            steps TEXT NOT NULL DEFAULT '[]',
            debug_payload TEXT
        )
    """)
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
            details TEXT NOT NULL DEFAULT ''
        )
    """)
    if runs:
        for r in runs:
            conn.execute(
                "INSERT INTO runs (run_id, project_id, project_name, module, status, ts_start, ts_end, "
                "duration_s, rows_out, group_id, steps, debug_payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    r.get("run_id", "run-1"),
                    r.get("project_id", ""),
                    r.get("project_name", ""),
                    r.get("module", "estrai"),
                    r.get("status", "success"),
                    r.get("ts_start", "2026-01-01T10:00:00"),
                    r.get("ts_end"),
                    r.get("duration_s"),
                    r.get("rows_out"),
                    r.get("group_id", ""),
                    r.get("steps", "[]"),
                    r.get("debug_payload"),
                )
            )
    if logs:
        for l in logs:
            conn.execute(
                "INSERT INTO logs (ts, level, module, op, msg, project_id, run_id, group_id, details) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    l.get("ts", "2026-01-01T10:00:01"),
                    l.get("level", "INFO"),
                    l.get("module", "estrai"),
                    l.get("op", "execute"),
                    l.get("msg", "Step OK"),
                    l.get("project_id", ""),
                    l.get("run_id", ""),
                    l.get("group_id", ""),
                    l.get("details", ""),
                )
            )
    conn.commit()
    conn.close()


@pytest.fixture
def run_db(tmp_path):
    """DB con run e log di test, patchato su runs_router."""
    db_path = tmp_path / "log.db"
    _create_db(db_path, runs=[
        {"run_id": "run-1", "project_id": "proj-1", "module": "estrai",  "status": "success",
         "ts_start": "2026-01-01T10:00:00", "ts_end": "2026-01-01T10:05:00",
         "duration_s": 300, "rows_out": 1000, "group_id": "grp-a",
         "steps": json.dumps([{"name": "extract", "status": "ok"}])},
        {"run_id": "run-2", "project_id": "proj-1", "module": "manipola", "status": "failure",
         "ts_start": "2026-01-01T11:00:00", "group_id": "grp-a",
         "debug_payload": json.dumps({"error": "timeout", "step": 2})},
        {"run_id": "run-3", "project_id": "proj-2", "module": "estrai",  "status": "running",
         "ts_start": "2026-01-02T08:00:00", "group_id": "grp-b"},
        {"run_id": "run-4", "project_id": "proj-3", "module": "arricchisci", "status": "success",
         "ts_start": "2026-01-02T09:00:00", "group_id": ""},
    ], logs=[
        {"ts": "2026-01-01T10:00:01", "level": "INFO",  "module": "estrai", "op": "execute",
         "msg": "Starting extraction", "run_id": "run-1", "group_id": "grp-a"},
        {"ts": "2026-01-01T10:02:00", "level": "INFO",  "module": "estrai", "op": "execute",
         "msg": "Rows fetched: 1000", "run_id": "run-1", "group_id": "grp-a"},
        {"ts": "2026-01-01T11:00:01", "level": "ERROR", "module": "manipola", "op": "transform",
         "msg": "Timeout after 300s", "run_id": "run-2", "group_id": "grp-a",
         "details": '{"elapsed": 300}'},
    ])
    with patch("app.core.logs.runs_router.LOG_DB_PATH", db_path):
        yield db_path


@pytest.fixture
def empty_db(tmp_path):
    fake_path = tmp_path / "nonexistent.db"
    with patch("app.core.logs.runs_router.LOG_DB_PATH", fake_path):
        yield fake_path


# =============================================================================
# _run_to_dict / _log_to_dict
# =============================================================================

class TestDictHelpers:

    def test_run_to_dict_deserializes_steps(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (run_id TEXT, steps TEXT, status TEXT, debug_payload TEXT)")
        conn.execute("INSERT INTO t VALUES ('r1', '[{\"name\":\"s1\"}]', 'success', NULL)")
        row = conn.execute("SELECT * FROM t").fetchone()
        d = _run_to_dict(row)
        assert d["steps"] == [{"name": "s1"}]
        assert "debug_payload" not in d

    def test_run_to_dict_invalid_steps_json(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (run_id TEXT, steps TEXT, status TEXT, debug_payload TEXT)")
        conn.execute("INSERT INTO t VALUES ('r1', 'not json', 'success', NULL)")
        row = conn.execute("SELECT * FROM t").fetchone()
        d = _run_to_dict(row)
        assert d["steps"] == []

    def test_run_to_dict_removes_debug_payload(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (run_id TEXT, steps TEXT, debug_payload TEXT)")
        conn.execute("INSERT INTO t VALUES ('r1', '[]', '{\"x\":1}')")
        row = conn.execute("SELECT * FROM t").fetchone()
        d = _run_to_dict(row)
        assert "debug_payload" not in d

    def test_run_to_dict_removes_empty_values(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (run_id TEXT, steps TEXT, module TEXT, debug_payload TEXT)")
        conn.execute("INSERT INTO t VALUES ('r1', '[]', '', NULL)")
        row = conn.execute("SELECT * FROM t").fetchone()
        d = _run_to_dict(row)
        assert "module" not in d

    def test_log_to_dict_deserializes_details(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (id INT, details TEXT, msg TEXT)")
        conn.execute("INSERT INTO t VALUES (1, '{\"a\":1}', 'hello')")
        row = conn.execute("SELECT * FROM t").fetchone()
        d = _log_to_dict(row)
        assert d["details"] == {"a": 1}

    def test_log_to_dict_invalid_json_kept(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (id INT, details TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'bad json')")
        row = conn.execute("SELECT * FROM t").fetchone()
        d = _log_to_dict(row)
        assert d["details"] == "bad json"


# =============================================================================
# GET /pipeline-runs — no filters
# =============================================================================

class TestGetRuns:

    def test_empty_db(self, empty_db):
        client = _make_client()
        res = client.get("/pipeline-runs")
        assert res.status_code == 200
        data = res.json()
        assert data["runs"] == []
        assert data["total"] == 0

    def test_returns_all_runs(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs")
        data = res.json()
        assert data["total"] == 4
        assert len(data["runs"]) == 4

    def test_pagination(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs?limit=2&offset=0")
        data = res.json()
        assert len(data["runs"]) == 2
        assert data["has_more"] is True

    def test_pagination_offset(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs?limit=100&offset=3")
        data = res.json()
        assert len(data["runs"]) == 1

    def test_order_desc(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs")
        runs = res.json()["runs"]
        ts_list = [r.get("ts_start", "") for r in runs]
        assert ts_list == sorted(ts_list, reverse=True)


# =============================================================================
# GET /pipeline-runs — filters
# =============================================================================

class TestGetRunsFilters:

    def test_filter_by_project_id(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs?project_id=proj-1")
        data = res.json()
        assert data["total"] == 2

    def test_filter_by_status(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs?status=success")
        data = res.json()
        assert data["total"] == 2
        for r in data["runs"]:
            assert r["status"] == "success"

    def test_filter_by_module(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs?module=estrai")
        data = res.json()
        assert data["total"] == 2

    def test_filter_by_after(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs?after=2026-01-02T00:00:00")
        data = res.json()
        assert data["total"] == 2

    def test_filter_by_before(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs?before=2026-01-01T11:00:00")
        data = res.json()
        assert data["total"] == 2

    def test_combined_filters(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs?project_id=proj-1&status=failure")
        data = res.json()
        assert data["total"] == 1
        assert data["runs"][0]["run_id"] == "run-2"


# =============================================================================
# GET /pipeline-runs — group isolation
# =============================================================================

class TestGetRunsGroupIsolation:

    def test_admin_sees_own_group_and_empty(self, run_db):
        user = _mock_user(role="admin", group_id="grp-a")
        client = _make_client(user=user)
        res = client.get("/pipeline-runs")
        data = res.json()
        # grp-a: run-1, run-2; group_id="": run-4 => 3
        assert data["total"] == 3

    def test_admin_cannot_see_other_group(self, run_db):
        user = _mock_user(role="admin", group_id="grp-a")
        client = _make_client(user=user)
        res = client.get("/pipeline-runs")
        for r in res.json()["runs"]:
            gid = r.get("group_id", "")
            assert gid in ("grp-a", ""), f"Unexpected group: {gid}"

    def test_superadmin_sees_all(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs")
        assert res.json()["total"] == 4


# =============================================================================
# GET /pipeline-runs/{run_id} — detail
# =============================================================================

class TestGetRunDetail:

    def test_success(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs/run-1")
        assert res.status_code == 200
        data = res.json()
        assert data["run_id"] == "run-1"
        assert data["status"] == "success"

    def test_includes_logs_by_default(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs/run-1")
        data = res.json()
        assert "logs" in data
        assert len(data["logs"]) == 2
        assert "logs_truncated" in data

    def test_exclude_logs(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs/run-1?include_logs=false")
        data = res.json()
        assert "logs" not in data

    def test_log_limit(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs/run-1?log_limit=1")
        data = res.json()
        assert len(data["logs"]) == 1
        assert data["logs_truncated"] is True

    def test_steps_deserialized(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs/run-1")
        data = res.json()
        assert isinstance(data["steps"], list)
        assert data["steps"][0]["name"] == "extract"

    def test_404_not_found(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs/nonexistent")
        assert res.status_code == 404

    def test_404_no_db(self, empty_db):
        client = _make_client()
        res = client.get("/pipeline-runs/run-1")
        assert res.status_code == 404

    def test_group_access_denied(self, run_db):
        user = _mock_user(role="admin", group_id="grp-b")
        client = _make_client(user=user)
        # run-1 belongs to grp-a
        res = client.get("/pipeline-runs/run-1")
        assert res.status_code == 403

    def test_superadmin_sees_any_group(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs/run-3")  # grp-b
        assert res.status_code == 200


# =============================================================================
# GET /pipeline-runs/{run_id}/payload
# =============================================================================

class TestGetRunPayload:

    def test_success_failure_run(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs/run-2/payload")
        assert res.status_code == 200
        data = res.json()
        assert data["run_id"] == "run-2"
        assert data["payload"]["error"] == "timeout"
        assert data["payload"]["step"] == 2

    def test_404_not_found(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs/nonexistent/payload")
        assert res.status_code == 404

    def test_404_no_db(self, empty_db):
        client = _make_client()
        res = client.get("/pipeline-runs/run-1/payload")
        assert res.status_code == 404

    def test_400_non_failure_run(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs/run-1/payload")  # status=success
        assert res.status_code == 400
        assert "fallite" in res.json()["detail"].lower() or "failure" in res.json()["detail"].lower()

    def test_404_no_payload_available(self, tmp_path):
        """Run failure ma senza debug_payload."""
        db_path = tmp_path / "log.db"
        _create_db(db_path, runs=[
            {"run_id": "run-nopayload", "status": "failure", "group_id": ""},
        ])
        with patch("app.core.logs.runs_router.LOG_DB_PATH", db_path):
            client = _make_client()
            res = client.get("/pipeline-runs/run-nopayload/payload")
            assert res.status_code == 404

    def test_500_invalid_json_payload(self, tmp_path):
        """debug_payload con JSON non valido."""
        db_path = tmp_path / "log.db"
        _create_db(db_path, runs=[
            {"run_id": "run-badjson", "status": "failure", "group_id": "",
             "debug_payload": "not valid json {"},
        ])
        with patch("app.core.logs.runs_router.LOG_DB_PATH", db_path):
            client = _make_client()
            res = client.get("/pipeline-runs/run-badjson/payload")
            assert res.status_code == 500

    def test_group_access_denied(self, run_db):
        user = _mock_user(role="admin", group_id="grp-b")
        client = _make_client(user=user)
        # run-2 belongs to grp-a
        res = client.get("/pipeline-runs/run-2/payload")
        assert res.status_code == 403

    def test_superadmin_can_access_any_group(self, run_db):
        client = _make_client()
        res = client.get("/pipeline-runs/run-2/payload")
        assert res.status_code == 200
