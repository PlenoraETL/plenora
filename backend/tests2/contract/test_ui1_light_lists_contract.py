"""UI-1 «Dati e polling» (06/11/2026) — liste leggere lato server.

Audit ui-velocita-chiamate-usabilita-raccolta-2026-11-06: C4, C5, C23, C24,
C25, C26. Un test per regola:

- GET /projects non legge i canvas quando la query mirata delle sorgenti
  risponde (C24); se la query non e' disponibile torna al percorso di prima;
- GET /files risponde senza `columns` (solo conteggio e primi nomi) e il
  client lo legge a pagine (C5, C26);
- GET /dashboard/counts conta con le stesse funzioni delle liste (C4);
- l'audit delle letture campionate scrive una riga per finestra (C23);
- la migrazione degli indici di log_runs esiste ed e' idempotente (C25).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.auth import require_not_esecutore
from app.core.projects.db import ManipolaSourceRefs
from app.shared.contracts.auth import CurrentUser

pytestmark = [pytest.mark.unit, pytest.mark.contract]

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def current_user() -> CurrentUser:
    return CurrentUser(user_id="u-1", username="tester", group_id="grp-1", role="admin")


def _project(project_id: str, project_type: str = "manipola") -> SimpleNamespace:
    base = {"id": project_id, "name": project_id, "project_type": project_type, "group_id": "grp-1"}
    ns = SimpleNamespace(**base)
    ns.model_dump = lambda: dict(base)
    return ns


def _projects_client(monkeypatch, current_user, projects, refs=None, refs_error=None):
    from app.core.projects import router as router_mod

    service = MagicMock()
    service.list_projects.return_value = SimpleNamespace(projects=projects)
    service.get_project_canvas.return_value = SimpleNamespace(nodes=[])
    monkeypatch.setattr(router_mod, "_project_service", service)
    monkeypatch.setattr(router_mod, "group_filter", lambda _cu: None)
    canvas_reader = MagicMock(return_value=[])
    monkeypatch.setattr(router_mod, "list_projects_with_canvas_for_datasource_check", canvas_reader)

    def fake_refs(ids):
        fake_refs.ids = set(ids)
        if refs_error is not None:
            raise refs_error
        return [ManipolaSourceRefs(project_id=k, source_project_ids=tuple(v["source_project_ids"]), file_source_ids=tuple(v["file_source_ids"]), corrupted=v["corrupted"]) for k, v in (refs or {}).items()]

    monkeypatch.setattr(router_mod, "list_manipola_source_refs", fake_refs)
    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: current_user
    app.include_router(router_mod.router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False), service, canvas_reader, fake_refs


def test_list_projects_uses_targeted_refs_and_reads_no_canvas(monkeypatch, current_user):
    client, service, canvas_reader, fake_refs = _projects_client(
        monkeypatch, current_user,
        [_project("m1"), _project("e1", "estrai")],
        refs={"m1": {"source_project_ids": ["e1"], "file_source_ids": ["f1"], "corrupted": False}},
    )
    body = client.get("/api/projects").json()
    by_id = {p["id"]: p for p in body["projects"]}
    assert by_id["m1"]["source_project_ids"] == ["e1"]
    assert by_id["m1"]["file_source_ids"] == ["f1"]
    assert "canvas_corrupted" not in by_id["m1"]
    # Solo i Manipola chiedono le sorgenti; nessun canvas letto.
    assert fake_refs.ids == {"m1"}
    canvas_reader.assert_not_called()
    service.get_project_canvas.assert_not_called()


def test_list_projects_marks_corrupted_canvas_from_targeted_query(monkeypatch, current_user):
    client, *_ = _projects_client(
        monkeypatch, current_user, [_project("m1")],
        refs={"m1": {"source_project_ids": [], "file_source_ids": [], "corrupted": True}},
    )
    p = client.get("/api/projects").json()["projects"][0]
    assert p["canvas_corrupted"] is True
    assert p["source_project_ids"] == []


def test_list_projects_falls_back_to_canvas_when_targeted_query_fails(monkeypatch, current_user):
    client, service, canvas_reader, _ = _projects_client(
        monkeypatch, current_user, [_project("m1")], refs_error=RuntimeError("pg_input_is_valid assente"),
    )
    response = client.get("/api/projects")
    assert response.status_code == 200
    canvas_reader.assert_called_once()
    assert response.json()["projects"][0]["source_project_ids"] == []


def test_targeted_refs_query_never_selects_the_canvas_column_into_python():
    from app.core.projects.db import ProjectRepository
    import inspect

    src = inspect.getsource(ProjectRepository.list_manipola_source_refs)
    assert "jsonb_array_elements" in src
    assert "pg_input_is_valid" in src
    # Il SELECT finale restituisce solo id, segno di canvas corrotto e liste.
    final_select = src.rsplit("SELECT p.id", 1)[1].split("FROM p\n", 1)[0]
    assert "canvas" not in final_select
    assert "p.doc IS NULL AS corrupted" in final_select


def test_files_list_has_no_columns_only_count_and_preview():
    from app.core.files.models import FileSource, FileSourceSummary, FILE_LIST_COLUMN_PREVIEW
    from app.shared.execution.column_info import ColumnInfo

    now = datetime(2026, 11, 6, tzinfo=timezone.utc)
    source = FileSource(
        id="f1", name="F", original_filename="f.csv", format="csv",
        columns=[ColumnInfo(name=f"c{i}") for i in range(12)],
        created_at=now, updated_at=now,
    )
    summary = FileSourceSummary.from_file_source(source)
    dumped = summary.model_dump()
    assert "columns" not in dumped
    assert dumped["column_count"] == 12
    assert dumped["column_names_preview"] == [f"c{i}" for i in range(FILE_LIST_COLUMN_PREVIEW)]


def test_files_list_route_declares_summary_model():
    from app.core.files import router as files_router
    from app.core.files.models import FileSourceSummaryList

    route = next(r for r in files_router.router.routes if getattr(r, "path", None) == "/files" and "GET" in r.methods)
    assert route.response_model is FileSourceSummaryList


def test_frontend_reads_files_list_in_pages():
    src = (BACKEND_ROOT.parent / "frontend" / "src" / "api" / "files.js").read_text(encoding="utf-8")
    # Conferma A-K10: a cursore (next_cursor), non a offset.
    assert re.search(r"getFiles\s*=\s*async\s*\(\)\s*=>\s*fetchAllCursorPages\('/files'", src)


def test_dashboard_counts_use_the_list_routes(monkeypatch, current_user):
    from app.core.arricchisci import router as api_routes
    from app.core.datasources import router as ds_routes
    from app.core.scheduler import router as sched_routes
    from app.core.dashboard import router as dashboard_mod

    calls = []

    def fake_api(_cu, **kw):
        calls.append(("api", kw["offset"]))
        # Due pagine: la prima piena.
        n = 500 if kw["offset"] == 0 else 3
        return {"api_connections": [{}] * n}

    def fake_ds(_cu, **kw):
        calls.append(("ds", kw["offset"]))
        return SimpleNamespace(datasources=[object(), object()])

    def fake_sched(**kw):
        calls.append(("sched", kw["offset"]))
        mk = lambda pid, en: SimpleNamespace(pipeline=SimpleNamespace(project_id=pid), schedule=SimpleNamespace(enabled=en))
        return SimpleNamespace(schedules=[mk("p1", True), mk("p1", False), mk("p2", True)])

    # Conferma C-K10: le funzioni di visibilita' delle liste, senza can_edit.
    monkeypatch.setattr(api_routes, "visible_api_connections", fake_api)
    monkeypatch.setattr(ds_routes, "visible_datasources", fake_ds)
    monkeypatch.setattr(sched_routes, "list_schedules", fake_sched)

    app = FastAPI()
    app.dependency_overrides[dashboard_mod.require_not_esecutore] = lambda: current_user
    app.include_router(dashboard_mod.router, prefix="/api")
    body = TestClient(app).get("/api/dashboard/counts").json()
    assert body == {
        "api_connections": 503,
        "datasources": 2,
        "schedules_active": 2,
        "schedules_by_project": {"p1": 2, "p2": 1},
        "unavailable": [],
    }
    assert ("api", 500) in calls


def test_dashboard_router_is_registered():
    # Lo snapshot OpenAPI e' verificato contro l'app vera da
    # tests2/contract/test_openapi_snapshot.py: basta che contenga la route.
    import json

    snapshot = json.loads((BACKEND_ROOT.parent / "frontend" / "tests" / "fixtures" / "openapi.snapshot.json").read_text(encoding="utf-8"))
    assert "/api/dashboard/counts" in snapshot["paths"]
    api_src = (BACKEND_ROOT / "app" / "core" / "api.py").read_text(encoding="utf-8")
    assert "api_router.include_router(dashboard_router)" in api_src


def test_sampled_read_audit_writes_one_row_per_window(monkeypatch):
    from app.observability import audit_action as mod

    rows = []
    monkeypatch.setattr(mod, "_write_audit_row", lambda *a: rows.append(a))
    mod._reset_read_samples_for_tests()
    user = SimpleNamespace(user_id="admin-1")

    @mod.audit_action(action="test.read", sample_reads=True)
    def read(current_user=None):
        return "ok"

    @mod.audit_action(action="test.write")
    def write(current_user=None):
        return "ok"

    for _ in range(5):
        read(current_user=user)
        write(current_user=user)
    assert [r[1] for r in rows].count("test.read") == 1
    assert [r[1] for r in rows].count("test.write") == 5
    mod._reset_read_samples_for_tests()


def test_system_status_read_audit_is_sampled_with_config_window():
    import inspect
    from app.config import Settings
    from app.core.admin_system_status import router as ss_router

    src = inspect.getsource(ss_router)
    assert 'audit_action(action="admin.system_status.read", sample_reads=True)' in src
    assert Settings.DEFAULTS["AUDIT_READ_SAMPLE_SECONDS"] > 0


def test_log_runs_latest_by_project_index_migration():
    path = BACKEND_ROOT / "migrations" / "2026-11-06_log_runs_latest_by_project_index.sql"
    sql = path.read_text(encoding="utf-8")
    statements = [s.strip() for s in sql.split(";") if s.strip() and not s.strip().startswith("--")]
    code = re.sub(r"--[^\n]*", "", sql)
    assert "ON log_runs (project_id, ts_start DESC)" in code
    assert "ON log_runs (group_id, project_id, ts_start DESC)" in code
    for stmt in [s for s in code.split(";") if s.strip()]:
        assert re.match(r"\s*CREATE INDEX CONCURRENTLY IF NOT EXISTS", stmt), stmt
    assert not re.search(r"\b(DROP|DELETE|UPDATE|TRUNCATE|ALTER)\b", code, re.I)
    assert statements
