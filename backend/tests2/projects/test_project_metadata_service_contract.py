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


# F3 — ProjectCanvasService


def test_facade_exposes_canvas_sub_service():
    from app.core.projects.canvas_service import ProjectCanvasService

    service = ProjectService()
    assert isinstance(service.canvas, ProjectCanvasService)


def test_update_canvas_via_facade_respects_lineage_patch(monkeypatch):
    """Il patch di classe storico su _assert_no_lineage_cycle deve
    restare efficace attraverso la delega al sub-service canvas."""
    from app.core.projects.canvas_models import CanvasState

    guard_calls: list[str] = []
    monkeypatch.setattr(
        ProjectService,
        "_assert_no_lineage_cycle",
        lambda self, project_id, canvas: guard_calls.append(project_id),
    )
    monkeypatch.setattr(
        ProjectService,
        "_find_dependent_projects",
        lambda self, project_id, group_id=None: [],
    )
    monkeypatch.setattr(
        "app.core.projects.canvas_service.project_db.get_project_row",
        lambda project_id, group_id=None: SimpleNamespace(group_id="grp-1"),
    )
    monkeypatch.setattr(
        "app.core.projects.canvas_service.project_db.update_project_row",
        lambda project_id, **fields: object(),
    )

    ok = ProjectService().update_canvas("p-1", CanvasState())

    assert ok is True
    assert guard_calls == ["p-1"]


def test_canvas_service_perimeter_is_closed():
    from app.core.projects.canvas_service import ProjectCanvasService

    public = {
        name
        for name in dir(ProjectCanvasService)
        if not name.startswith("_")
    }
    assert public == {
        "get_project_canvas",
        "get_project_raw_canvas",
        "update_canvas",
        "set_canvas_validated",
        "create_canvas_version",
        "list_canvas_versions",
        "restore_canvas_version",
    }, f"perimetro inatteso: {sorted(public)}"


def test_raw_project_canvas_reexport_identity():
    """Il re-export compatibile in service punta alla stessa classe."""
    from app.core.projects import canvas_service, service

    assert service.RawProjectCanvas is canvas_service.RawProjectCanvas


# F5 — ProjectLifecycleService


def test_facade_exposes_lifecycle_sub_service():
    from app.core.projects.lifecycle_service import ProjectLifecycleService

    service = ProjectService()
    assert isinstance(service.lifecycle, ProjectLifecycleService)


def test_delete_project_via_facade_respects_dependent_patch(monkeypatch):
    """Il patch di classe su _find_dependent_projects resta efficace nel
    blocco dipendenze della delete (delega owner-backref)."""
    from app.core.projects.exceptions import ProjectHasDependenciesError

    monkeypatch.setattr(
        "app.core.projects.lifecycle_service.project_db.get_project_row",
        lambda project_id, group_id=None: SimpleNamespace(group_id="grp-1"),
    )
    monkeypatch.setattr(
        "app.core.projects.lifecycle_service.list_eseguibili_by_source",
        lambda project_id: [],
    )
    monkeypatch.setattr(
        ProjectService,
        "_find_dependent_projects",
        lambda self, project_id, group_id=None: [
            {"id": "dep-1", "name": "Dep", "project_type": "manipola"}
        ],
    )

    with pytest.raises(ProjectHasDependenciesError):
        ProjectService().delete_project("p-1")


def test_clone_project_chains_through_facade_methods(monkeypatch):
    """clone passa dai metodi della facade (owner-backref): i patch sui
    metodi pubblici intercettano ogni step."""
    calls: list[str] = []
    cloned_meta = SimpleNamespace(id="p-new")

    monkeypatch.setattr(
        ProjectService,
        "get_project_metadata",
        lambda self, project_id, group_id=None: SimpleNamespace(
            description="d", project_type="manipola", default_connection_id=None,
        ),
    )
    monkeypatch.setattr(
        ProjectService,
        "create_project",
        lambda self, data, group_id=None, created_by=None: (
            calls.append("create"), cloned_meta
        )[1],
    )
    monkeypatch.setattr(
        ProjectService,
        "get_project_canvas",
        lambda self, project_id: None,
    )
    monkeypatch.setattr(
        ProjectService,
        "get_project_output_contract",
        lambda self, project_id: None,
    )

    result = ProjectService().clone_project("p-src", "Nuovo")

    assert result is cloned_meta
    assert calls == ["create"]


def test_lifecycle_service_perimeter_is_closed():
    from app.core.projects.lifecycle_service import ProjectLifecycleService

    public = {
        name
        for name in dir(ProjectLifecycleService)
        if not name.startswith("_")
    }
    assert public == {
        "clone_project",
        "delete_project",
        "find_dependent_projects",
    }, f"perimetro inatteso: {sorted(public)}"


def test_dependency_payload_reexport_identity():
    from app.core.projects import lifecycle_service, service

    assert (
        service.ProjectDependencyPayload
        is lifecycle_service.ProjectDependencyPayload
    )


# F4 — ProjectOutputContractService


def test_facade_exposes_output_contract_sub_service():
    from app.core.projects.output_contract_service import (
        ProjectOutputContractService,
    )

    service = ProjectService()
    assert isinstance(service.output_contract, ProjectOutputContractService)


def test_facade_output_contract_reads_delegate(monkeypatch):
    contract = {"columns": [{"name": "a", "type": "string"}]}
    monkeypatch.setattr(
        "app.core.projects.output_contract_service.project_db.get_project_row",
        lambda project_id: SimpleNamespace(output_contract=contract),
    )

    assert ProjectService().get_project_output_contract("p-1") == contract


def test_facade_output_contract_update_delegates(monkeypatch):
    captured: dict = {}

    def _update(project_id, *, output_contract):
        captured["project_id"] = project_id
        captured["contract"] = output_contract
        return object()

    monkeypatch.setattr(
        "app.core.projects.output_contract_service.project_db."
        "update_project_output_contract_row",
        _update,
    )

    ok = ProjectService().update_project_output_contract(
        "p-1", {"columns": []}
    )

    assert ok is True
    assert captured == {"project_id": "p-1", "contract": {"columns": []}}


def test_output_contract_method_patch_on_facade_still_wins(monkeypatch):
    """I 7 patch di metodo storici su ProjectService.* devono restare
    efficaci: la delega non bypassa il metodo della facade."""
    monkeypatch.setattr(
        ProjectService,
        "get_project_output_contract",
        lambda self, project_id: {"patched": True},
    )
    assert ProjectService().get_project_output_contract("x") == {
        "patched": True
    }


def test_output_contract_service_perimeter_is_closed():
    from app.core.projects.output_contract_service import (
        ProjectOutputContractService,
    )

    public = {
        name
        for name in dir(ProjectOutputContractService)
        if not name.startswith("_")
    }
    assert public == {
        "get_project_output_contract",
        "update_project_output_contract",
    }, f"perimetro inatteso: {sorted(public)}"
