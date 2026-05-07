from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_project_list_db_leaf_returns_immutable_project_rows(monkeypatch):
    from app.db.config import projects as project_db

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
        )
    ]
    with pytest.raises(FrozenInstanceError):
        rows[0].name = "changed"


def test_project_detail_db_leaf_returns_immutable_project_row(monkeypatch):
    from app.db.config import projects as project_db

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
    )
    with pytest.raises(FrozenInstanceError):
        row.name = "changed"


def test_get_project_keeps_legacy_dict_wrapper(monkeypatch):
    from app.db.config import projects as project_db

    monkeypatch.setattr(
        project_db,
        "get_project_row",
        lambda *args, **kwargs: project_db.ProjectDetailRow(
            id="p1",
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

    row = project_db.get_project("p1")

    assert isinstance(row, dict)
    assert row["id"] == "p1"
    assert row["canvas"] == {"nodes": [], "edges": []}


def test_project_service_list_projects_uses_typed_project_rows(monkeypatch):
    from app.db.config import projects as project_db
    from app.services.project_service import ProjectService

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

    def fail_if_legacy_dict_converter_is_used(self, row):
        raise AssertionError("list_projects must consume ProjectRow, not raw dict")

    monkeypatch.setattr(
        ProjectService,
        "_row_to_metadata",
        fail_if_legacy_dict_converter_is_used,
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
            }
        ]
    }
