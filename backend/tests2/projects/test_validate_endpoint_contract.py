"""Contract — POST /projects/{id}/validate (Epic Canvas Validation, endpoint read-only).

L'endpoint orizzontale: carica il canvas salvato → validate_canvas → ValidationReport.
Read-only, non blocca nulla. Registry pulito → strutturale-only deterministico.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.auth import require_not_esecutore
from app.core.projects import router as projects_router_module
from app.core.projects.router import router as projects_router
from app.shared.contracts.auth import CurrentUser


pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.fixture(autouse=True)
def _clean_registry():
    from app.shared.execution import validation_protocol as vp
    vp.clear_node_validators()
    yield
    vp.clear_node_validators()


def _user() -> CurrentUser:
    return CurrentUser(user_id="u", username="t", group_id="g", role="admin")


def _node(node_id, ntype):
    n = MagicMock(); n.id = node_id; n.type = ntype; n.data = {}
    return n


def _edge(source, target):
    e = MagicMock(); e.source = source; e.target = target
    e.id = f"{source}->{target}"
    e.source_column = ""; e.target_column = ""; e.is_spatial = False
    e.source_geom_column = None; e.target_geom_column = None
    return e


def _canvas(nodes, edges):
    c = MagicMock(); c.nodes = nodes; c.edges = edges
    return c


def _client(canvas, monkeypatch) -> TestClient:
    svc = MagicMock()
    svc.get_project_metadata.return_value = MagicMock(id="p", group_id="g")
    svc.get_project_canvas.return_value = canvas
    monkeypatch.setattr(projects_router_module, "_project_service", svc)
    monkeypatch.setattr(projects_router_module, "assert_group_access", lambda *a, **k: None)
    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: _user()
    app.include_router(projects_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def test_validate_empty_canvas_is_invalid(monkeypatch):
    client = _client(_canvas([], []), monkeypatch)
    res = client.post("/api/projects/p/validate")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["valid"] is False
    assert body["has_errors"] is True
    assert any(i["code"] == "CANVAS_EMPTY" for i in body["issues"])
    # stato persistito = esito: canvas invalido → canvas_validated False (§12)
    projects_router_module._project_service.set_canvas_validated.assert_called_once_with("p", False)


def test_validate_clean_canvas_is_valid(monkeypatch):
    nodes = [_node("s", "file_source"), _node("r", "result")]
    client = _client(_canvas(nodes, [_edge("s", "r")]), monkeypatch)
    res = client.post("/api/projects/p/validate")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["valid"] is True
    assert body["issues"] == []
    # canvas valido → canvas_validated True (§12)
    projects_router_module._project_service.set_canvas_validated.assert_called_once_with("p", True)


def test_validate_reports_api_connection_stale(monkeypatch):
    # endpoint inietta gli id connessioni esistenti; api_id non presente → STALE.
    e = _node("e", "api_enrich"); e.data = {"api_id": "gone"}
    nodes = [_node("s", "file_source"), e, _node("r", "result")]
    canvas = _canvas(nodes, [_edge("s", "e"), _edge("e", "r")])

    svc = MagicMock()
    svc.get_project_metadata.return_value = MagicMock(id="p", group_id="g", project_type="arricchisci")
    svc.get_project_canvas.return_value = canvas
    monkeypatch.setattr(projects_router_module, "_project_service", svc)
    monkeypatch.setattr(projects_router_module, "assert_group_access", lambda *a, **k: None)
    # connessione "gone" NON tra quelle esistenti
    monkeypatch.setattr(
        "app.core.arricchisci.db.list_api_connection_rows",
        lambda *a, **k: [SimpleNamespace(id="other")],
    )
    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: _user()
    app.include_router(projects_router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=False)

    res = client.post("/api/projects/p/validate")
    assert res.status_code == 200, res.text
    body = res.json()
    assert any(i["code"] == "ARRICCHISCI_API_CONNECTION_STALE" for i in body["issues"])


def test_validate_reports_cycle(monkeypatch):
    nodes = [_node("a", "transform"), _node("b", "transform"), _node("r", "result")]
    edges = [_edge("a", "b"), _edge("b", "a"), _edge("b", "r")]
    client = _client(_canvas(nodes, edges), monkeypatch)
    res = client.post("/api/projects/p/validate")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["valid"] is False
    assert any(i["code"] == "lineage.cycle" for i in body["issues"])
