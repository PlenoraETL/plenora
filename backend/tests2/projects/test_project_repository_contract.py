from __future__ import annotations

from datetime import datetime, timezone

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _detail_row(project_db, project_id: str = "p1"):
    now = datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc)
    return project_db.ProjectDetailRow(
        id=project_id,
        group_id="grp-1",
        name="Project One",
        project_type="estrai",
        canvas={"nodes": [], "edges": []},
        connection_id="conn-1",
        created_at=now,
        updated_at=now,
        created_by="user-1",
    )


def test_project_repository_covers_core_project_persistence_methods(monkeypatch):
    from app.core.projects import db as project_db

    now = datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc)
    executed: list[tuple[str, tuple[object, ...]]] = []

    class _Cursor:
        rowcount = 1

    def fake_fetchone(sql, params):
        assert "projects" in sql
        return {
            "id": params[0],
            "group_id": "grp-1",
            "name": "Project One",
            "project_type": "estrai",
            "canvas": '{"nodes": [], "edges": []}',
            "connection_id": "conn-1",
            "created_at": now,
            "updated_at": now,
            "created_by": "user-1",
        }

    def fake_fetchall(sql, params=()):
        assert "projects" in sql
        return [
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
        ]

    def fake_execute(sql, params=()):
        executed.append((sql, params))
        return _Cursor()

    monkeypatch.setattr(project_db, "fetchone", fake_fetchone)
    monkeypatch.setattr(project_db, "fetchall", fake_fetchall)
    monkeypatch.setattr(project_db, "execute", fake_execute)

    repo = project_db.ProjectRepository()

    assert repo.get_project_row("p1").id == "p1"
    assert repo.list_projects("grp-1")[0].id == "p1"
    assert repo.create_project_row(
        project_id="p1",
        group_id="grp-1",
        name="Project One",
        project_type="estrai",
        connection_id="conn-1",
        created_by="user-1",
    ).id == "p1"
    assert repo.update_project_row("p1", name="Renamed").id == "p1"
    assert repo.delete_project("p1", group_id="grp-1") is True

    assert any(sql.startswith("INSERT INTO projects") for sql, _ in executed)
    assert any(sql.startswith("UPDATE projects SET") for sql, _ in executed)
    assert any(sql.startswith("DELETE FROM projects") for sql, _ in executed)


def test_project_module_functions_delegate_to_repository_object(monkeypatch):
    from app.core.projects import db as project_db

    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    class _Repository:
        def get_project_row(self, *args, **kwargs):
            calls.append(("get_project_row", args, kwargs))
            return _detail_row(project_db, args[0])

        def list_projects(self, *args, **kwargs):
            calls.append(("list_projects", args, kwargs))
            return []

        def create_project_row(self, *args, **kwargs):
            calls.append(("create_project_row", args, kwargs))
            return _detail_row(project_db, kwargs["project_id"])

        def update_project_row(self, *args, **kwargs):
            calls.append(("update_project_row", args, kwargs))
            return _detail_row(project_db, args[0])

        def delete_project(self, *args, **kwargs):
            calls.append(("delete_project", args, kwargs))
            return True

    monkeypatch.setattr(project_db, "project_repository", _Repository())

    assert project_db.get_project_row("p1").id == "p1"
    assert project_db.list_projects("grp-1") == []
    assert project_db.create_project_row(
        project_id="p2",
        group_id="grp-1",
        name="Project Two",
        project_type="estrai",
    ).id == "p2"
    assert project_db.update_project_row("p1", name="Renamed").id == "p1"
    assert project_db.delete_project("p1", group_id="grp-1") is True

    assert [name for name, _, _ in calls] == [
        "get_project_row",
        "list_projects",
        "create_project_row",
        "update_project_row",
        "delete_project",
    ]
