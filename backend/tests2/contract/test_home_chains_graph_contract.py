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
- la route e' nello snapshot OpenAPI ed e' riservata a chi non e' esecutore;
- «Visibile a» (10/11/2026): `access` dice gruppo proprietario e condivisioni
  di Eseguibili e origini con la visibilita' delle liste
  (`visible_resource_ids`); l'elenco delle condivisioni solo al superadmin
  (`shared_with` null agli altri); `groups` nomina i gruppi della risposta;
  il progetto porta il suo `group_id`.
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


def _meta(pid: str, name: str, ptype: str, conn: str | None = None, group: str = "grp-1"):
    return SimpleNamespace(id=pid, name=name, project_type=ptype, default_connection_id=conn, group_id=group,
                           tags=[], updated_at="2026-11-09T10:00:00+00:00", canvas={"nodes": []})


# Il mondo delle condivisioni dei test: proprietari, righe di condivisione,
# nomi dei gruppi. ds1 e' di un gruppo che non lo condivide: un utente di
# grp-1 non lo vede (la lista dei Data Source non lo mostrerebbe).
OWNERS = {
    "eseguibili": {"x-1": "grp-1"},
    "connections": {"c1": "grp-2"},
    "arcgis_connections": {},
    "file_sources": {"f1": "grp-1"},
    "datasources": {"ds1": "grp-9"},
}
SHARES = {"eseguibili": {"x-1": ["grp-3"]}, "connections": {"c1": ["*"]}}
GROUP_NAMES = {"grp-1": "Uffici", "grp-2": "Patrimonio", "grp-3": "Ospiti", "grp-9": "Altro"}


def _visible(table: str, group: str) -> set[str]:
    shares = SHARES.get(table, {})
    return {rid for rid, owner in OWNERS[table].items()
            if owner == group or group in shares.get(rid, []) or "*" in shares.get(rid, [])}


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
                 "active_version": 2, "updated_at": "2026-11-08", "source_changed": True,
                 "canvas": {"nodes": []}}] if offset == 0 else []

    monkeypatch.setattr(eseguibili_routes, "list_eseguibili", fake_eseguibili)

    def fake_schedules(project_id, limit, offset, current_user):
        calls.items.append(("schedules", current_user.user_id, offset))
        if "schedules" in fail:
            raise RuntimeError("giu'")
        mk = lambda pid, en: SimpleNamespace(pipeline=SimpleNamespace(project_id=pid), schedule=SimpleNamespace(enabled=en))
        return SimpleNamespace(schedules=[mk("e1", True), mk("e1", False), mk("altro", True)])

    monkeypatch.setattr(schedules_routes, "list_schedules", fake_schedules)

    from app.core.dashboard import graph as graph_mod

    def fake_visible(table, group_id, is_superadmin=False):
        calls.items.append(("visible", table, group_id))
        if "access" in fail:
            raise RuntimeError("giu'")
        return _visible(table, group_id)

    def fake_owners(table, ids):
        calls.items.append(("owners", table, frozenset(ids)))
        if "access" in fail:
            raise RuntimeError("giu'")
        return {rid: OWNERS[table][rid] for rid in ids if rid in OWNERS[table]}

    def fake_shares(table, ids):
        calls.items.append(("shares", table, frozenset(ids)))
        return {rid: SHARES.get(table, {})[rid] for rid in ids if rid in SHARES.get(table, {})}

    def fake_group_names(ids):
        calls.items.append(("groups", frozenset(ids)))
        if "groups" in fail:
            raise RuntimeError("giu'")
        return {g: GROUP_NAMES[g] for g in ids if g in GROUP_NAMES}

    monkeypatch.setattr(graph_mod.auth_visibility, "visible_resource_ids", fake_visible)
    monkeypatch.setattr(graph_mod.auth_shares, "resource_owner_groups", fake_owners)
    monkeypatch.setattr(graph_mod.auth_shares, "resource_shares_by_id", fake_shares)
    monkeypatch.setattr(graph_mod.auth_db, "group_names", fake_group_names)

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
        # N1: lo stesso campo della lista degli Eseguibili (service.source_changed, P2).
        "source_changed_since_publish": True,
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


@pytest.mark.parametrize("part", ["links", "runs", "eseguibili", "schedules", "access", "groups"])
def test_graph_part_failure_is_declared_and_others_stay(monkeypatch, part):
    client, _ = _client(monkeypatch, _user(), fail=(part,))
    body = client.get("/api/dashboard/graph").json()
    assert body["unavailable"] == [part]
    assert {p["id"] for p in body["projects"]} == {"e1", "m1", "x1"}
    if part != "eseguibili":
        assert len(body["eseguibili"]) == 1
    if part != "links":
        assert {p["id"]: p for p in body["projects"]}["m1"]["source_project_ids"] == ["e1"]


def test_graph_without_projects_is_empty_and_projects_failure_fails_the_response(monkeypatch):
    """P6: come dice la docstring del modulo — senza progetti la risposta e'
    valida e vuota; la lettura dei progetti non e' una parte (`unavailable`)."""
    from app.core.dashboard import graph as graph_mod
    from app.core.projects import router as projects_routes

    assert "senza progetti la risposta e' valida e vuota" in graph_mod.__doc__
    assert "senza progetti la risposta e' un errore" not in graph_mod.__doc__
    client, _ = _client(monkeypatch, _user())
    projects_routes._project_service.list_projects.side_effect = (
        lambda group_id, limit, offset: SimpleNamespace(projects=[]))
    body = client.get("/api/dashboard/graph").json()
    assert body["projects"] == [] and body["unavailable"] == []
    projects_routes._project_service.list_projects.side_effect = RuntimeError("giu'")
    with pytest.raises(RuntimeError):
        client.get("/api/dashboard/graph")


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


# ── «Visibile a» (10/11/2026) ────────────────────────────────────────────


def _access(body):
    return {(a["resource_type"], a["id"]): a for a in body["access"]}


def test_graph_parts_are_declared():
    from app.core.dashboard import graph as graph_mod
    from app.core.auth.visibility import _SHAREABLE

    assert graph_mod.GRAPH_PARTS == ("links", "runs", "eseguibili", "schedules", "access", "groups")
    # Le tabelle di «Visibile a» sono tabelle condivisibili vere.
    assert set(graph_mod.ACCESS_TABLES) <= set(_SHAREABLE)
    assert set(graph_mod.CONNECTION_TABLES.values()) <= set(graph_mod.ACCESS_TABLES)


def test_superadmin_sees_owner_and_shares_of_every_node(monkeypatch):
    client, calls = _client(monkeypatch, _user("superadmin", "grp-1"))
    body = client.get("/api/dashboard/graph").json()
    assert body["unavailable"] == []
    assert _access(body) == {
        ("eseguibili", "x-1"): {"resource_type": "eseguibili", "id": "x-1", "group_id": "grp-1", "shared_with": ["grp-3"]},
        ("connections", "c1"): {"resource_type": "connections", "id": "c1", "group_id": "grp-2", "shared_with": ["*"]},
        ("file_sources", "f1"): {"resource_type": "file_sources", "id": "f1", "group_id": "grp-1", "shared_with": []},
        ("datasources", "ds1"): {"resource_type": "datasources", "id": "ds1", "group_id": "grp-9", "shared_with": []},
    }
    # Il superadmin vede tutto: nessun filtro di visibilita'.
    assert not [c for c in calls.items if c[0] == "visible"]
    # Nomi: proprietari e gruppi delle condivisioni (non «*»).
    assert body["groups"] == {"grp-1": "Uffici", "grp-2": "Patrimonio", "grp-3": "Ospiti", "grp-9": "Altro"}
    assert {p["id"]: p["group_id"] for p in body["projects"]} == {"e1": "grp-1", "m1": "grp-1", "x1": "grp-1"}


@pytest.mark.parametrize("role", ["admin", "user"])
def test_others_see_owner_but_not_the_share_list(monkeypatch, role):
    client, calls = _client(monkeypatch, _user(role, "grp-1"))
    body = client.get("/api/dashboard/graph").json()
    assert body["unavailable"] == []
    access = _access(body)
    # Solo cio' che le liste mostrerebbe: ds1 (di grp-9, non condiviso) manca.
    assert set(access) == {("eseguibili", "x-1"), ("connections", "c1"), ("file_sources", "f1")}
    assert access[("connections", "c1")]["group_id"] == "grp-2"
    # Le condivisioni le legge e le gestisce solo il superadmin.
    assert all(a["shared_with"] is None for a in access.values())
    assert not [c for c in calls.items if c[0] == "shares"]
    # La visibilita' delle liste, con il gruppo dell'utente, tabella per tabella.
    assert {c[1] for c in calls.items if c[0] == "visible"} == {"eseguibili", "connections", "file_sources", "datasources"}
    assert {c[2] for c in calls.items if c[0] == "visible"} == {"grp-1"}
    # Nessun nome di gruppo che la risposta non nomina (grp-3 della condivisione, grp-9).
    assert body["groups"] == {"grp-1": "Uffici", "grp-2": "Patrimonio"}


def test_access_asks_only_the_graph_resources(monkeypatch):
    client, calls = _client(monkeypatch, _user("superadmin", "grp-1"))
    client.get("/api/dashboard/graph")
    owners = {c[1]: c[2] for c in calls.items if c[0] == "owners"}
    assert owners == {
        "eseguibili": frozenset({"x-1"}),
        "connections": frozenset({"c1"}),
        "file_sources": frozenset({"f1"}),
        "datasources": frozenset({"ds1"}),
    }


def test_shares_batch_queries(monkeypatch):
    from app.core.auth import db as auth_db
    from app.core.auth import share_store, shares

    seen = []

    def fake_fetchall(sql, params=()):
        seen.append((sql, params))
        if "group_id FROM eseguibile_shares" in sql:
            return [{"resource_id": "x-1", "group_id": "grp-3"}, {"resource_id": "x-1", "group_id": "*"}]
        return [{"id": "x-1", "group_id": "grp-1"}]

    monkeypatch.setattr(share_store, "_pg_fetchall", fake_fetchall)
    assert shares.resource_owner_groups("eseguibili", {"x-1", "x-1"}) == {"x-1": "grp-1"}
    assert shares.resource_shares_by_id("eseguibili", ["x-1"]) == {"x-1": ["grp-3", "*"]}
    assert seen[0] == ("SELECT id, group_id FROM eseguibili WHERE id = ANY(%s)", (["x-1"],))
    assert "WHERE eseguibile_id = ANY(%s)" in seen[1][0]
    # Niente query senza id; tabella non condivisibile rifiutata.
    assert shares.resource_owner_groups("eseguibili", []) == {}
    assert len(seen) == 2
    with pytest.raises(ValueError):
        shares.resource_owner_groups("projects", ["p1"])

    monkeypatch.setattr(auth_db, "_fetchall", lambda sql, params=(): (seen.append((sql, params)), [{"id": "grp-1", "name": "Uffici"}])[1])
    assert auth_db.group_names({"grp-1", ""}) == {"grp-1": "Uffici"}
    assert seen[-1] == ("SELECT id, name FROM groups WHERE id = ANY(%s)", (["grp-1"],))
    assert auth_db.group_names([]) == {}


@real_pg_marks
def test_shares_batch_queries_on_real_postgres(monkeypatch):
    from app.core.auth import db as auth_db
    from app.core.auth import share_store, shares

    with real_pg_schema() as conn:
        conn.execute("CREATE TABLE eseguibili (id text primary key, group_id text)")
        conn.execute("CREATE TABLE eseguibile_shares (eseguibile_id text, group_id text)")
        conn.execute("CREATE TABLE groups (id text primary key, name text)")
        conn.execute("INSERT INTO eseguibili VALUES ('x-1', 'grp-1'), ('x-2', 'grp-2')")
        conn.execute("INSERT INTO eseguibile_shares VALUES ('x-1', 'grp-3'), ('x-1', '*'), ('x-2', 'grp-1')")
        conn.execute("INSERT INTO groups VALUES ('grp-1', 'Uffici'), ('grp-2', 'Patrimonio')")
        run = lambda sql, params=(): list(conn.execute(sql, params).fetchall())
        monkeypatch.setattr(share_store, "_pg_fetchall", run)
        monkeypatch.setattr(auth_db, "_fetchall", run)
        assert shares.resource_owner_groups("eseguibili", ["x-1", "nope"]) == {"x-1": "grp-1"}
        assert shares.resource_shares_by_id("eseguibili", ["x-1", "x-2"]) == {"x-1": ["*", "grp-3"], "x-2": ["grp-1"]}
        assert auth_db.group_names(["grp-1", "grp-x"]) == {"grp-1": "Uffici"}
