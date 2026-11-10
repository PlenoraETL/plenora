"""Titoli delle catene della home modificabili (09/11/2026).

Il titolo calcolato (nome del nodo finale con il cammino piu' lungo,
frontend/src/utils/projectChains.js) resta il default; chi puo' modificare i
metadati del progetto rinomina la catena. Persistenza: colonna
``projects.chain_title`` (i metadati sono colonne, nessuna colonna JSON libera
li ospita: migrazione 2026-11-09_add_project_chain_title.sql, solo ADD COLUMN
IF NOT EXISTS), scritta da PATCH /projects/{id} (stesso permesso dei
metadati, editProjectMeta) ed esposta per progetto da GET /dashboard/graph.
Vuoto = NULL = titolo calcolato.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.projects import db as project_db
from app.core.projects import service as project_service_mod
from app.shared.contracts.auth import CurrentUser

pytestmark = [pytest.mark.unit, pytest.mark.contract]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND_ROOT / "migrations" / "2026-11-09_add_project_chain_title.sql"


def _user() -> CurrentUser:
    return CurrentUser(user_id="u-1", username="tester", group_id="grp-1", role="admin")


def _row(**over) -> dict:
    return {"id": "p1", "group_id": "grp-1", "name": "fine", "project_type": "manipola", "connection_id": None,
            "created_at": None, "updated_at": None, "created_by": "u-1", "description": "", "tags": [], **over}


def test_migration_only_adds_a_nullable_column():
    sql = "\n".join(line for line in MIGRATION.read_text(encoding="utf-8").splitlines()
                    if not line.strip().startswith("--"))
    assert " ".join(sql.split()) == "ALTER TABLE projects ADD COLUMN IF NOT EXISTS chain_title TEXT;"


def test_rows_and_metadata_carry_chain_title():
    row = project_db.ProjectRow.from_mapping(_row(chain_title="Patrimonio"))
    detail = project_db.ProjectDetailRow.from_mapping(_row(chain_title="Patrimonio", canvas="{}"))
    service = project_service_mod.ProjectService
    assert service.project_row_to_metadata(row).chain_title == "Patrimonio"
    assert service.project_detail_row_to_metadata(detail).chain_title == "Patrimonio"
    # Colonna assente o NULL: titolo calcolato.
    assert service.project_row_to_metadata(project_db.ProjectRow.from_mapping(_row())).chain_title is None


def test_list_query_reads_the_column(monkeypatch):
    captured: list[str] = []
    monkeypatch.setattr(project_db, "_fetchall", lambda sql, *a, **k: captured.append(sql) or [])
    project_db.project_repository.list_projects(group_id="grp-1")
    assert "chain_title" in captured[0]


@pytest.mark.parametrize("given,written", [
    ("  Patrimonio  ", "Patrimonio"),
    ("", None),          # svuota: torna il titolo calcolato
    ("   ", None),
    ("x" * 300, "x" * project_service_mod.CHAIN_TITLE_MAX),
])
def test_update_details_normalizes_chain_title(monkeypatch, given, written):
    seen: dict = {}

    def fake_update(project_id, **fields):
        seen.update(fields)
        return project_db.ProjectDetailRow.from_mapping(_row(chain_title=fields.get("chain_title"), canvas="{}"))

    monkeypatch.setattr(project_db, "update_project_row", fake_update)
    service = project_service_mod.ProjectService.__new__(project_service_mod.ProjectService)
    out = service.update_project_details("p1", chain_title=given)
    assert seen == {"chain_title": written}
    assert out.chain_title == written


def test_update_details_without_chain_title_does_not_touch_it(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(project_db, "update_project_row",
                        lambda project_id, **fields: seen.update(fields) or project_db.ProjectDetailRow.from_mapping(_row(canvas="{}")))
    service = project_service_mod.ProjectService.__new__(project_service_mod.ProjectService)
    service.update_project_details("p1", tags=["a"])
    assert "chain_title" not in seen


def test_patch_route_passes_chain_title(monkeypatch):
    from app.core.auth import dependencies as auth_deps
    from app.core.projects import router as projects_routes

    service = MagicMock()
    service.get_project_metadata.return_value = SimpleNamespace(group_id="grp-1")
    service.update_project_details.return_value = SimpleNamespace(model_dump=lambda: {"id": "p1", "chain_title": "T"})
    monkeypatch.setattr(projects_routes, "_project_service", service)
    monkeypatch.setattr(projects_routes, "assert_group_access", lambda *a, **k: None)
    app = FastAPI()
    app.dependency_overrides[auth_deps.get_current_user] = _user
    app.include_router(projects_routes.router)
    response = TestClient(app).patch("/projects/p1", json={"chain_title": "T"})
    assert response.status_code == 200, response.text
    assert service.update_project_details.call_args.kwargs["chain_title"] == "T"


def test_dashboard_graph_exposes_chain_title_per_project(monkeypatch):
    from app.core.dashboard import graph as graph_mod
    from app.core.projects import router as projects_routes

    service = MagicMock()
    service.list_projects.return_value = None
    monkeypatch.setattr(projects_routes, "_project_service", service)
    monkeypatch.setattr(graph_mod.auth_db, "group_names", lambda ids: {})
    metas = [
        SimpleNamespace(id="p1", name="fine", project_type="manipola", default_connection_id=None, tags=[],
                        updated_at=None, chain_title="Patrimonio"),
        SimpleNamespace(id="p2", name="estr", project_type="estrai", default_connection_id=None, tags=[],
                        updated_at=None, chain_title=None),
    ]
    graph = graph_mod.build_dashboard_graph(_user(), all_items=lambda page, field: metas if field == "projects" else [])
    assert {p.id: p.chain_title for p in graph.projects} == {"p1": "Patrimonio", "p2": None}
    assert "chain_title" in graph_mod.GraphProject.model_fields
