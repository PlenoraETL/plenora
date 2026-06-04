from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_project_list_db_leaf_returns_immutable_project_rows(monkeypatch):
    from app.core.projects import db as project_db

    now = datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        project_db,
        "fetchall",
        lambda *args, **kwargs: [
            {
                "id": "p1",
                "group_id": "grp-1",
                "name": "Project One",
                "project_type": "estrai",
                "connection_id": "conn-1",
                "created_at": now,
                "updated_at": now,
                "created_by": "user-1",
            }
        ],
    )

    rows = project_db.list_projects("grp-1")

    assert rows == [
        project_db.ProjectRow(
            id="p1",
            group_id="grp-1",
            name="Project One",
            project_type="estrai",
            connection_id="conn-1",
            created_at=now,
            updated_at=now,
            created_by="user-1",
            tags=[],
        )
    ]
    with pytest.raises(FrozenInstanceError):
        rows[0].name = "changed"


def test_project_detail_db_leaf_returns_immutable_project_row(monkeypatch):
    from app.core.projects import db as project_db

    now = datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        project_db,
        "fetchone",
        lambda *args, **kwargs: {
            "id": "p1",
            "group_id": "grp-1",
            "name": "Project One",
            "project_type": "estrai",
            "canvas": '{"nodes": [], "edges": []}',
            "connection_id": "conn-1",
            "created_at": now,
            "updated_at": now,
            "created_by": "user-1",
            "retention_n": 3,
        },
    )

    row = project_db.get_project_row("p1", group_id="grp-1")

    assert row == project_db.ProjectDetailRow(
        id="p1",
        group_id="grp-1",
        name="Project One",
        project_type="estrai",
        canvas={"nodes": [], "edges": []},
        connection_id="conn-1",
        created_at=now,
        updated_at=now,
        created_by="user-1",
        retention_n=3,
        tags=[],
    )
    with pytest.raises(FrozenInstanceError):
        row.name = "changed"


def test_project_db_leaf_does_not_expose_legacy_get_project_wrapper():
    from app.core.projects import db as project_db

    assert not hasattr(project_db, "get_project")


def test_project_create_db_leaf_returns_typed_row(monkeypatch):
    from app.core.projects import db as project_db

    now = datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc)
    executed: list[tuple[str, tuple[object, ...]]] = []

    def fake_execute(sql, params):
        executed.append((sql, params))

    monkeypatch.setattr(project_db, "execute", fake_execute)
    monkeypatch.setattr(
        project_db,
        "get_project_row",
        lambda project_id: project_db.ProjectDetailRow(
            id=project_id,
            group_id="grp-1",
            name="Project One",
            project_type="estrai",
            canvas={"nodes": [], "edges": []},
            connection_id="conn-1",
            created_at=now,
            updated_at=now,
            created_by="user-1",
        ),
    )

    typed_row = project_db.create_project_row(
        project_id="p1",
        group_id="grp-1",
        name="Project One",
        project_type="estrai",
        connection_id="conn-1",
        created_by="user-1",
    )

    assert isinstance(typed_row, project_db.ProjectDetailRow)
    assert typed_row.id == "p1"
    assert executed
    assert not hasattr(project_db, "create_project")


def test_project_update_db_leaf_returns_typed_row(monkeypatch):
    from app.core.projects import db as project_db

    now = datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc)
    executed: list[tuple[str, tuple[object, ...]]] = []

    def fake_execute(sql, params):
        executed.append((sql, params))

    monkeypatch.setattr(project_db, "execute", fake_execute)
    monkeypatch.setattr(
        project_db,
        "get_project_row",
        lambda project_id: project_db.ProjectDetailRow(
            id=project_id,
            group_id="grp-1",
            name="Project One",
            project_type="estrai",
            canvas={"nodes": [], "edges": []},
            connection_id=None,
            created_at=now,
            updated_at=now,
            created_by=None,
        ),
    )

    typed_row = project_db.update_project_row(
        "p1", canvas={"nodes": [], "edges": []}
    )

    assert isinstance(typed_row, project_db.ProjectDetailRow)
    assert typed_row.id == "p1"
    assert executed
    assert not hasattr(project_db, "update_project")


def test_project_dependency_db_leaf_returns_typed_rows_without_legacy_wrapper(monkeypatch):
    from app.core.projects import db as project_db

    monkeypatch.setattr(
        project_db,
        "fetchall",
        lambda *args, **kwargs: [
            {
                "id": "p1",
                "name": "Project One",
                "project_type": "estrai",
                "group_id": "grp-1",
            }
        ],
    )

    typed_rows = project_db.list_project_dependency_rows_using_connection("conn-1")

    assert typed_rows == [
        project_db.ProjectDependencyRow(
            id="p1",
            name="Project One",
            project_type="estrai",
            group_id="grp-1",
        )
    ]
    with pytest.raises(FrozenInstanceError):
        typed_rows[0].name = "changed"

    assert not hasattr(project_db, "list_projects_using_connection")


def test_project_canvas_check_db_leaf_returns_typed_rows_without_legacy_wrapper(monkeypatch):
    from app.core.projects import db as project_db

    monkeypatch.setattr(
        project_db,
        "fetchall",
        lambda *args, **kwargs: [
            {
                "id": "p1",
                "name": "Project One",
                "project_type": "manipola",
                "canvas": '{"nodes": []}',
                "group_id": "grp-1",
            }
        ],
    )

    typed_rows = project_db.list_project_canvas_check_rows()

    assert typed_rows == [
        project_db.ProjectCanvasCheckRow(
            id="p1",
            name="Project One",
            project_type="manipola",
            canvas='{"nodes": []}',
            group_id="grp-1",
        )
    ]
    with pytest.raises(FrozenInstanceError):
        typed_rows[0].name = "changed"

    assert not hasattr(project_db, "list_projects_with_canvas_for_datasource_check")


def test_project_service_list_projects_uses_typed_project_rows(monkeypatch):
    from app.core.projects import db as project_db
    from app.core.projects.service import ProjectService

    now = datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(ProjectService, "_ensure_default_group", lambda self: None)
    monkeypatch.setattr(
        project_db,
        "list_projects",
        lambda group_id=None: [
            project_db.ProjectRow(
                id="p1",
                group_id=None,
                name="Project One",
                project_type="estrai",
                connection_id="conn-1",
                created_at=now,
                updated_at=now,
                created_by="user-1",
            )
        ],
    )

    result = ProjectService().list_projects()

    assert result.model_dump() == {
        "projects": [
            {
                "id": "p1",
                "name": "Project One",
                "description": "",
                "project_type": "estrai",
                "default_connection_id": "conn-1",
                "group_id": "default",
                "created_at": now,
                "updated_at": now,
                "canvas_validated": False,
                "tags": [],
            }
        ]
    }


def test_project_service_create_project_uses_typed_post_write_row(monkeypatch):
    from app.core.projects import db as project_db
    from app.core.projects.service import ProjectService
    from app.core.projects.models import ProjectCreate

    now = datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(ProjectService, "_ensure_default_group", lambda self: None)
    monkeypatch.setattr(ProjectService, "_generate_project_id", lambda self, name: "p1")
    monkeypatch.setattr(
        project_db,
        "create_project_row",
        lambda **kwargs: project_db.ProjectDetailRow(
            id=kwargs["project_id"],
            group_id=kwargs["group_id"],
            name=kwargs["name"],
            project_type=kwargs["project_type"],
            canvas={"nodes": [], "edges": []},
            connection_id=kwargs["connection_id"],
            created_at=now,
            updated_at=now,
            created_by=kwargs["created_by"],
        ),
    )

    assert not hasattr(project_db, "create_project")

    result = ProjectService().create_project(
        ProjectCreate(
            name="Project One",
            project_type="estrai",
            default_connection_id="conn-1",
        ),
        group_id="grp-1",
        created_by="user-1",
    )

    assert result.model_dump() == {
        "id": "p1",
        "name": "Project One",
        "description": "",
        "project_type": "estrai",
        "default_connection_id": "conn-1",
        "group_id": "grp-1",
        "created_at": now,
        "updated_at": now,
        "canvas_validated": False,
        "tags": [],
    }


def test_project_service_metadata_uses_typed_project_detail_row(monkeypatch):
    from app.core.projects import db as project_db
    from app.core.projects.service import ProjectService

    now = datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        project_db,
        "get_project_row",
        lambda project_id, group_id=None: project_db.ProjectDetailRow(
            id=project_id,
            group_id=None,
            name="Project One",
            project_type="manipola",
            canvas={"nodes": [], "edges": []},
            connection_id="conn-1",
            created_at=now,
            updated_at=now,
            created_by="user-1",
            description="typed detail",
        ),
    )

    assert not hasattr(project_db, "get_project")

    result = ProjectService().get_project_metadata("p1")

    assert result is not None
    assert result.model_dump() == {
        "id": "p1",
        "name": "Project One",
        "description": "typed detail",
        "project_type": "manipola",
        "default_connection_id": "conn-1",
        "group_id": "default",
        "created_at": now,
        "updated_at": now,
        "canvas_validated": False,
        "tags": [],
    }


def test_project_service_exposes_explicit_typed_row_serializers():
    from app.core.projects import db as project_db
    from app.core.projects.service import ProjectService

    now = datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc)

    list_metadata = ProjectService.project_row_to_metadata(
        project_db.ProjectRow(
            id="p-list",
            group_id=None,
            name="List Project",
            project_type="estrai",
            connection_id="conn-1",
            created_at=now,
            updated_at=now,
            created_by="user-1",
        )
    )
    detail_metadata = ProjectService.project_detail_row_to_metadata(
        project_db.ProjectDetailRow(
            id="p-detail",
            group_id="grp-1",
            name="Detail Project",
            project_type="manipola",
            canvas={"nodes": [], "edges": []},
            connection_id=None,
            created_at=now,
            updated_at=now,
            created_by="user-1",
            description="typed detail",
        )
    )

    assert list_metadata.model_dump() == {
        "id": "p-list",
        "name": "List Project",
        "description": "",
        "project_type": "estrai",
        "default_connection_id": "conn-1",
        "group_id": "default",
        "created_at": now,
        "updated_at": now,
        "canvas_validated": False,
        "tags": [],
    }
    assert detail_metadata.model_dump() == {
        "id": "p-detail",
        "name": "Detail Project",
        "description": "typed detail",
        "project_type": "manipola",
        "default_connection_id": None,
        "group_id": "grp-1",
        "created_at": now,
        "updated_at": now,
        "canvas_validated": False,
        "tags": [],
    }


def test_project_service_get_project_converts_typed_row_at_public_compat_edge(
    monkeypatch,
):
    from app.core.projects import db as project_db
    from app.core.projects.service import ProjectService

    monkeypatch.setattr(
        project_db,
        "get_project_row",
        lambda project_id, group_id=None: project_db.ProjectDetailRow(
            id=project_id,
            group_id="grp-1",
            name="Project One",
            project_type="estrai",
            canvas={"nodes": [], "edges": []},
            connection_id=None,
            created_at=None,
            updated_at=None,
            created_by=None,
        ),
    )

    assert not hasattr(project_db, "get_project")

    result = ProjectService().get_project("p1", group_id="grp-1")

    assert result == {
        "metadata": {
            "id": "p1",
            "name": "Project One",
            "description": "",
            "project_type": "estrai",
            "default_connection_id": None,
            "group_id": "grp-1",
            "created_at": None,
            "updated_at": None,
            "canvas_validated": False,
            "tags": [],
        },
        "canvas": {"nodes": [], "edges": []},
        "id": "p1",
    }


def test_project_service_update_canvas_uses_typed_post_write_row(monkeypatch):
    from app.core.projects import db as project_db
    from app.core.projects.service import ProjectService
    from app.core.projects.canvas_models import CanvasState

    monkeypatch.setattr(
        ProjectService,
        "_assert_no_lineage_cycle",
        lambda self, project_id, canvas: None,
    )
    monkeypatch.setattr(
        project_db,
        "get_project_row",
        lambda project_id, group_id=None: project_db.ProjectDetailRow(
            id=project_id,
            group_id="grp-1",
            name="Project One",
            project_type="estrai",
            canvas={"nodes": [], "edges": []},
            connection_id=None,
            created_at=None,
            updated_at=None,
            created_by=None,
        ),
    )
    monkeypatch.setattr(
        project_db,
        "update_project_row",
        lambda project_id, **fields: project_db.ProjectDetailRow(
            id=project_id,
            group_id="grp-1",
            name="Project One",
            project_type="estrai",
            canvas=fields["canvas"],
            connection_id=None,
            created_at=None,
            updated_at=None,
            created_by=None,
        ),
    )

    assert not hasattr(project_db, "update_project")

    # update_canvas cascata l'invalidazione ai progetti a valle (Epic CanvasVal
    # §12) → la scansione delle dipendenze non deve toccare il DB reale.
    monkeypatch.setattr(project_db, "list_project_canvas_check_rows", lambda: [])

    assert ProjectService().update_canvas("p1", CanvasState()) is True
