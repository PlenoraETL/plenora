"""Contract — stato di validazione canvas persistito + cascade (Epic CanvasVal §12).

- update_canvas azzera ``canvas_validated`` del progetto E lo cascata a False sui
  progetti a valle (ogni salvataggio a monte invalida la catena certificata).
- set_canvas_validated persiste l'esito (chiamato dall'endpoint con not has_errors).
"""

from __future__ import annotations

import pytest

from app.core.projects import db as project_db
from app.core.projects.canvas_models import CanvasState
from app.core.projects.service import ProjectService


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _row(pid="p1", group_id="grp-1"):
    return project_db.ProjectDetailRow(
        id=pid, group_id=group_id, name="P", project_type="estrai",
        canvas={"nodes": [], "edges": []}, connection_id=None,
        created_at=None, updated_at=None, created_by=None,
    )


def test_update_canvas_resets_own_flag_and_cascades_to_dependents(monkeypatch):
    monkeypatch.setattr(ProjectService, "_assert_no_lineage_cycle", lambda *a, **k: None)
    monkeypatch.setattr(project_db, "get_project_row", lambda pid, group_id=None: _row(pid))

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        project_db, "update_project_row",
        lambda pid, **fields: (calls.append((pid, fields)), _row(pid))[1],
    )
    monkeypatch.setattr(
        ProjectService, "_find_dependent_projects",
        lambda self, project_id, group_id=None: [
            {"id": "dep1", "name": "d1", "project_type": "manipola"},
            {"id": "dep2", "name": "d2", "project_type": "arricchisci"},
        ],
    )

    assert ProjectService().update_canvas("p1", CanvasState()) is True

    by_pid = {pid: fields for pid, fields in calls}
    assert by_pid["p1"]["canvas_validated"] is False  # progetto salvato → non più certificato
    assert by_pid["dep1"]["canvas_validated"] is False  # cascade a valle
    assert by_pid["dep2"]["canvas_validated"] is False


def test_set_canvas_validated_persists_true(monkeypatch):
    monkeypatch.setattr(project_db, "get_project_row", lambda pid, group_id=None: _row(pid))
    captured: dict[str, dict] = {}
    monkeypatch.setattr(
        project_db, "update_project_row",
        lambda pid, **fields: (captured.__setitem__(pid, fields), _row(pid))[1],
    )
    assert ProjectService().set_canvas_validated("p1", True) is True
    assert captured["p1"]["canvas_validated"] is True


def test_set_canvas_validated_false_on_missing_project(monkeypatch):
    monkeypatch.setattr(project_db, "get_project_row", lambda pid, group_id=None: None)
    assert ProjectService().set_canvas_validated("ghost", True) is False


def test_detail_row_reads_canvas_validated_from_db_row():
    row = project_db.ProjectDetailRow.from_mapping({
        "id": "p1", "group_id": "g", "name": "P", "project_type": "estrai",
        "canvas": None, "connection_id": None, "created_at": None,
        "updated_at": None, "created_by": None, "canvas_validated": True,
    })
    assert row.canvas_validated is True
    md = ProjectService.project_detail_row_to_metadata(row)
    assert md.canvas_validated is True
