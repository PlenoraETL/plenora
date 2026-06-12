"""
Contratto F2 (Epic F roadmap): ProjectMetadataService estratto dalla facade.

Blinda tre proprieta' dell'estrazione:
1. la facade delega al sub-service (stessa semantica, stessa superficie);
2. i monkeypatch storici sulla classe facade (_ensure_default_group,
   converter row->metadata) restano efficaci attraverso la delega;
3. il sub-service resta a perimetro stretto (sole letture metadata) e
   soddisfa strutturalmente il port auth.ports.ProjectMetadataReader.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.projects.metadata_service import ProjectMetadataService
from app.core.projects.service import ProjectService

pytestmark = [pytest.mark.unit, pytest.mark.projects, pytest.mark.contract]


def _row(project_id: str = "p-1") -> SimpleNamespace:
    return SimpleNamespace(
        id=project_id,
        name="Demo",
        description="d",
        project_type="manipola",
        connection_id=None,
        group_id="grp-1",
        created_at=None,
        updated_at=None,
        canvas_validated=False,
        tags=None,
    )


def test_facade_exposes_metadata_sub_service():
    service = ProjectService()
    assert isinstance(service.metadata, ProjectMetadataService)


def test_facade_list_projects_delegates_and_respects_class_patches(monkeypatch):
    """Il patch di classe su _ensure_default_group (storico nei contract
    typed-rows) deve restare efficace anche con la delega al sub-service."""
    ensure_calls: list[bool] = []
    monkeypatch.setattr(
        ProjectService,
        "_ensure_default_group",
        lambda self: ensure_calls.append(True),
    )
    monkeypatch.setattr(
        "app.core.projects.metadata_service.project_db.list_projects",
        lambda group_id: [_row("p-1"), _row("p-2")],
    )

    result = ProjectService().list_projects("grp-1")

    assert ensure_calls == [True]
    assert [p.id for p in result.projects] == ["p-1", "p-2"]


def test_facade_get_project_metadata_delegates(monkeypatch):
    monkeypatch.setattr(
        "app.core.projects.metadata_service.project_db.get_project_row",
        lambda project_id, group_id=None: _row(project_id),
    )

    meta = ProjectService().get_project_metadata("p-9")

    assert meta is not None
    assert meta.id == "p-9"
    assert meta.group_id == "grp-1"


def test_facade_project_exists_delegates(monkeypatch):
    monkeypatch.setattr(
        "app.core.projects.metadata_service.project_db.get_project_row",
        lambda project_id, group_id=None: None,
    )
    assert ProjectService().project_exists("ghost") is False


def test_metadata_service_satisfies_project_metadata_reader_port():
    """Il sub-service e' un ProjectMetadataReader strutturale (Epic C):
    i chiamanti auth possono consumarlo senza la facade."""
    from app.core.auth.ports import ProjectMetadataReader

    service = ProjectService()
    assert isinstance(service.metadata, ProjectMetadataReader)


def test_metadata_service_perimeter_stays_read_only():
    """Niente scritture/canvas/lifecycle nel sub-service: le nuove
    responsabilita' vanno in sub-service dedicati, non qui."""
    public = {
        name
        for name in dir(ProjectMetadataService)
        if not name.startswith("_")
    }
    assert public == {
        "list_projects",
        "get_project_metadata",
        "project_exists",
    }, f"perimetro inatteso: {sorted(public)}"
