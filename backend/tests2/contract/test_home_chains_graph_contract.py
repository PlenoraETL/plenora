"""Home per catene (09/11/2026) — GET /dashboard/graph.

Un test per regola:
- la risposta ha progetti (sorgenti, connessioni, Data Source, Carica, ultima
  esecuzione, ultima riuscita, pianificazioni) ed Eseguibili (progetto
  sorgente, input), e nessun canvas;
- la visibilita' e' quella delle liste: progetti con `group_filter` come
  GET /projects, Eseguibili dalla route GET /eseguibili, pianificazioni dalla
  route GET /schedules, esecuzioni con `latest_runs_by_project`, sempre con
  l'utente della richiesta;
- una parte che fallisce e' in `unavailable`, le altre restano;
- la query dei collegamenti non porta il canvas in Python e legge gli alias
  dei tipi di nodo;
- `latest_runs_by_project` filtra gruppo ed esito;
- la route e' nello snapshot OpenAPI ed e' riservata a chi non e' esecutore.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.projects.db import ProjectGraphRefs
from app.shared.contracts.auth import CurrentUser
from tests2.shared._real_pg import real_pg_marks, real_pg_schema

pytestmark = [pytest.mark.unit, pytest.mark.contract]

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _user(role: str = "admin", group: str = "grp-1") -> CurrentUser:
    return CurrentUser(user_id="u-1", username="tester", group_id=group, role=role)


def _meta(pid: str, name: str, ptype: str, conn: str | None = None):
    return SimpleNamespace(id=pid, name=name, project_type=ptype, default_connection_id=conn,
                           tags=[], updated_at="2026-11-09T10:00:00+00:00", canvas={"nodes": []})


class _Calls:
    def __init__(self):
        self.items = []


def _client(monkeypatch, user, *, fail=()):
    from app.core.dashboard import router as dashboard_mod
    from app.core.eseguibili import router as eseguibili_routes
    from app.core.logs import runs_router as runs_routes
    from app.core.projects import router as projects_routes
    from app.core.projects import usage as projects_usage
    from app.core.scheduler import router as schedules_routes

    calls = _Calls()
    service = MagicMock()
    service.list_projects.side_effect = lambda group_id, limit, offset: (
        calls.items.append(("projects", group_id, offset)),
        SimpleNamespace(projects=[
            _meta("e1", "immobili attivi", "estrai", "c1"),
            _meta("m1", "immobili attivi per istituto", "manipola"),
            _meta("x1", "SC_H_ticket", "estrai", "c1"),
        ] if offset == 0 else []),
    )[1]
    monkeypatch.setattr(projects_routes, "_project_service", service)

    def fake_refs(ids):
        calls.items.append(("links", frozenset(ids)))
        if "links" in fail:
            raise RuntimeError("giu'")
        return [
            ProjectGraphRefs(project_id="e1", source_project_ids=(), file_source_ids=(),
                             connections=(("c1", "db"),), datasources=(), loads=("db",), tables=("afm_rm",), corrupted=False),
            ProjectGraphRefs(project_id="m1", source_project_ids=("e1",), file_source_ids=("f1",),
                             connections=(), datasources=(("ds1", "SharePoint"),), loads=(), tables=(), corrupted=False),
        ]

    monkeypatch.setattr(projects_usage, "list_project_graph_refs", fake_refs)

    def fake_runs(cu, *, status=None):
        calls.items.append(("runs", cu.user_id, status))
        if "runs" in fail:
            raise RuntimeError("giu'")
        if status == "success":
            return {"e1": {"status": "success", "ts_start": "2026-11-07T08:00:00+00:00"}}
        return {
            "e1": {"status": "success", "ts_start": "2026-11-07T08:00:00+00:00"},
            "m1": {"status": "failure", "ts_start": "2026-11-09T08:00:00+00:00"},
        }

    monkeypatch.setattr(runs_routes, "latest_runs_by_project", fake_runs)

    def fake_eseguibili(limit, offset, current_user):
        calls.items.append(("eseguibili", current_user.user_id, offset))
        if "eseguibili" in fail:
            raise RuntimeError("giu'")
        return [{"id": "x-1", "name": "immobili attivi per istituto", "project_type": "manipola",
                 "source_canvas_id": "m1", "inputs_count": 0, "has_manifest": True,
                 "active_version": 2, "updated_at": "2026-11-08", "canvas": {"nodes": []}}] if offset == 0 else []

    monkeypatch.setattr(eseguibili_routes, "list_eseguibili", fake_eseguibili)

    def fake_schedules(project_id, limit, offset, current_user):
        calls.items.append(("schedules", current_user.user_id, offset))
        if "schedules" in fail:
            raise RuntimeError("giu'")
        mk = lambda pid, en: SimpleNamespace(pipeline=SimpleNamespace(project_id=pid), schedule=SimpleNamespace(enabled=en))
        return SimpleNamespace(schedules=[mk("e1", True), mk("e1", False), mk("altro", True)])

    monkeypatch.setattr(schedules_routes, "list_schedules", fake_schedules)

    app = FastAPI()
    app.dependency_overrides[dashboard_mod.require_not_esecutore] = lambda: user
    app.include_router(dashboard_mod.router, prefix="/api")
    return TestClient(app), calls


def test_graph_returns_nodes_and_links_without_canvas(monkeypatch):
    client, _ = _client(monkeypatch, _user())
    response = client.get("/api/dashboard/graph")
    assert response.status_code == 200
    body = response.json()
    # Nessun canvas (ne' nodi) in nessun oggetto della risposta.
    def keys(value):
        if isinstance(value, dict):
            for k, v in value.items():
                yield k
                yield from keys(v)
        elif isinstance(value, list):
            for v in value:
                yield from keys(v)
    assert not {"canvas", "nodes", "edges"} & set(keys(body))
    by_id = {p["id"]: p for p in body["projects"]}
    assert set(by_id) == {"e1", "m1", "x1"}
    assert by_id["e1"]["connections"] == [{"id": "c1", "kind": "db"}]
    assert by_id["e1"]["default_connection_id"] == "c1"
    assert by_id["e1"]["loads"] == ["db"]
    assert by_id["e1"]["tables"] == ["afm_rm"]
    assert by_id["e1"]["schedules"] == 2 and by_id["e1"]["schedules_active"] == 1
    assert by_id["e1"]["last_success_at"] == "2026-11-07T08:00:00+00:00"
    assert by_id["m1"]["source_project_ids"] == ["e1"]
    assert by_id["m1"]["file_source_ids"] == ["f1"]
    assert by_id["m1"]["datasources"] == [{"id": "ds1", "name": "SharePoint"}]
    assert by_id["m1"]["last_run"] == {"status": "failure", "at": "2026-11-09T08:00:00+00:00"}
    assert by_id["m1"]["last_success_at"] is None
    assert by_id["x1"]["last_run"] is None
    assert body["eseguibili"] == [{
        "id": "x-1", "name": "immobili attivi per istituto", "project_type": "manipola",
        "source_project_id": "m1", "inputs_count": 0, "has_manifest": True,
        "active_version": 2, "updated_at": "2026-11-08",
    }]
    assert body["unavailable"] == []


@pytest.mark.parametrize("role,group,expected", [("admin", "grp-1", "grp-1"), ("user", "grp-2", "grp-2"), ("superadmin", "grp-1", None)])
def test_graph_uses_the_list_visibility(monkeypatch, role, group, expected):
    client, calls = _client(monkeypatch, _user(role, group))
    assert client.get("/api/dashboard/graph").status_code == 200
    # Progetti come GET /projects: group_filter dell'utente.
    assert ("projects", expected, 0) in calls.items
    # Eseguibili, esecuzioni, pianificazioni: le route e la regola delle
    # liste, con l'utente della richiesta.
    assert ("eseguibili", "u-1", 0) in calls.items
    assert ("schedules", "u-1", 0) in calls.items
    assert ("runs", "u-1", None) in calls.items and ("runs", "u-1", "success") in calls.items
    # I collegamenti solo dei progetti visibili.
    assert ("links", frozenset({"e1", "m1", "x1"})) in calls.items


@pytest.mark.parametrize("part", ["links", "runs", "eseguibili", "schedules"])
def test_graph_part_failure_is_declared_and_others_stay(monkeypatch, part):
    client, _ = _client(monkeypatch, _user(), fail=(part,))
    body = client.get("/api/dashboard/graph").json()
    assert body["unavailable"] == [part]
    assert {p["id"] for p in body["projects"]} == {"e1", "m1", "x1"}
    if part != "eseguibili":
        assert len(body["eseguibili"]) == 1
    if part != "links":
        assert {p["id"]: p for p in body["projects"]}["m1"]["source_project_ids"] == ["e1"]


def test_graph_route_is_not_for_esecutori():
    from app.core.dashboard import router as dashboard_mod

    route = next(r for r in dashboard_mod.router.routes if getattr(r, "path", "") == "/dashboard/graph")
    deps = [d.call for d in route.dependant.dependencies]
    assert dashboard_mod.require_not_esecutore in deps


def test_graph_refs_query_reads_no_canvas_and_normalizes_aliases():
    from app.core.projects.db import PROJECT_GRAPH_REFS_SQL

    src = PROJECT_GRAPH_REFS_SQL
    assert "pg_input_is_valid" in src and "jsonb_array_elements" in src
    final_select = src.rsplit("SELECT p.id", 1)[1].split("FROM p\n", 1)[0]
    assert "canvas" not in final_select
    for alias, canonical in [("projectSource", "project_source"), ("fileSource", "file_source"),
                             ("tableNode", "source"), ("featureLayerNode", "feature_source"),
                             ("datasourceNode", "datasource_node"), ("caricaArcgis", "carica_arcgis")]:
        assert f"WHEN '{alias}' THEN '{canonical}'" in src
    # Tutti i tipi di progetto, non solo Manipola.
    assert "project_type" not in src.split("FROM projects", 1)[1].split(")", 1)[0]


def test_graph_refs_rows_are_typed_and_sorted(monkeypatch):
    from app.core.projects import db as project_db

    rows = [{"id": "p1", "corrupted": False,
             "source_project_ids": json.dumps(["b", "a"]), "file_source_ids": [],
             "connections": [["c2", "arcgis"], ["c1", "db"]],
             "datasources": [["ds", None]], "loads": ["rest", "db"], "tables": ["t2", "t1"]}]
    monkeypatch.setattr(project_db, "_fetchall", lambda sql, params: rows)
    [refs] = project_db.list_project_graph_refs({"p1"})
    assert refs.source_project_ids == ("a", "b")
    assert refs.connections == (("c1", "db"), ("c2", "arcgis"))
    assert refs.datasources == (("ds", ""),)
    assert refs.loads == ("db", "rest")
    assert refs.tables == ("t1", "t2")
    assert project_db.list_project_graph_refs(set()) == []


@real_pg_marks
@pytest.mark.parametrize("column_type", ["jsonb", "text"])
def test_graph_refs_on_real_postgres(monkeypatch, column_type):
    """La query dei collegamenti su PostgreSQL vero, colonna jsonb e text."""
    from app.core.projects import db as projects_db

    canvas = json.dumps({"nodes": [
        {"type": "project_source", "data": {"source_project_id": "s1"}},
        {"type": "projectSource", "source_project_id": "s2"},
        {"type": "project_source", "source_project_id": "s1"},
        {"type": "file_source", "file_source_id": "f1"},
        {"type": "source", "connection_id": "c1", "table_name": "afm_rm"},
        {"type": "tableNode", "data": {"connection_id": "c1", "table_name": "afm_bl"}},
        {"type": "feature_source", "connection_id": "g1"},
        {"type": "datasource_node", "datasource_id": "d1", "datasource_name": "SharePoint"},
        {"type": "datasourceNode", "mode": "carica", "datasource_id": "d2"},
        {"type": "carica", "dest_type": "db", "table_name": "out_t"},
        {"type": "carica", "dest_type": "rest"},
        {"type": "caricaArcgis"},
        {"type": "transform"},
    ]})
    with real_pg_schema() as conn:
        conn.execute(f"CREATE TABLE projects (id text primary key, project_type text, canvas {column_type})")
        rows = [("p1", "manipola", canvas), ("p2", "estrai", '{"edges": []}')]
        if column_type == "text":
            rows.append(("p3", "estrai", "{non e' json"))
        for row in rows:
            conn.execute("INSERT INTO projects VALUES (%s, %s, %s)", row)
        monkeypatch.setattr(projects_db, "_fetchall", lambda sql, params=(): list(conn.execute(sql, params).fetchall()))
        refs = {r.project_id: r for r in projects_db.list_project_graph_refs({r[0] for r in rows})}

    p1 = refs["p1"]
    assert p1.source_project_ids == ("s1", "s2")
    assert p1.file_source_ids == ("f1",)
    assert p1.connections == (("c1", "db"), ("g1", "arcgis"))
    assert p1.datasources == (("d1", "SharePoint"),)
    assert p1.loads == ("arcgis", "datasource", "db", "rest")
    assert p1.tables == ("afm_bl", "afm_rm", "out_t")
    assert p1.corrupted is False
    assert refs["p2"].source_project_ids == () and refs["p2"].loads == () and refs["p2"].corrupted is False
    if column_type == "text":
        assert refs["p3"].corrupted is True


@pytest.mark.parametrize("role,status", [("admin", None), ("admin", "success"), ("superadmin", "success")])
def test_latest_runs_by_project_filters_group_and_status(monkeypatch, role, status):
    from app.core.logs import runs_router

    seen = {}

    def fake_fetchall(sql, params):
        seen["sql"], seen["params"] = sql, params
        return [{"project_id": "p1", "status": "success", "ts_start": "t"}, {"project_id": "", "status": "x"}]

    monkeypatch.setattr(runs_router, "_fetchall", fake_fetchall)
    out = runs_router.latest_runs_by_project(_user(role), status=status)
    assert out == {"p1": {"status": "success", "ts_start": "t"}}
    assert ("status = %s" in seen["sql"]) is bool(status)
    assert ("group_id = %s" in seen["sql"]) is (role != "superadmin")
    assert "DISTINCT ON (project_id)" in seen["sql"]


def test_graph_route_in_openapi_snapshot():
    snapshot = json.loads((BACKEND_ROOT.parent / "frontend" / "tests" / "fixtures" / "openapi.snapshot.json").read_text(encoding="utf-8"))
    assert "/api/dashboard/graph" in snapshot["paths"]
