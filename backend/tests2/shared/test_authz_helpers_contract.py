"""
Tests the authz helpers behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

import pytest
from fastapi import HTTPException

from app.shared.contracts.auth import CurrentUser
from app.shared.authz_helpers import (
    assert_canvas_resources_access,
    assert_file_source_access,
    assert_project_access,
    assert_project_visible,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


@pytest.fixture
def current_user() -> CurrentUser:
    return CurrentUser(
        user_id="u-1",
        username="tester",
        group_id="grp-1",
        role="admin",
    )


def test_assert_canvas_resources_access_checks_file_sources_and_project_sources(monkeypatch, current_user):
    seen = []

    import app.shared.authz_helpers as authz

    monkeypatch.setattr(authz, "assert_file_source_access", lambda file_source_id, user: seen.append(("file", file_source_id, user.group_id)))
    monkeypatch.setattr(authz, "assert_project_visible", lambda project_id, user: seen.append(("project", project_id, user.group_id)))

    canvas = {
        "nodes": [
            {"type": "file_source", "file_source_id": "fs-1"},
            {"type": "project_source", "source_project_id": "proj-2"},
            {"type": "api_enrich", "extra_files": {"config": {"file_source_id": "fs-3"}}},
        ]
    }

    assert_canvas_resources_access(canvas, current_user)

    assert seen == [
        ("file", "fs-1", "grp-1"),
        ("project", "proj-2", "grp-1"),
        ("file", "fs-3", "grp-1"),
    ]


def test_assert_canvas_resources_access_stops_on_first_denied_resource(monkeypatch, current_user):
    import app.shared.authz_helpers as authz

    def _deny(file_source_id, user):
        raise HTTPException(status_code=403, detail=f"denied:{file_source_id}")

    monkeypatch.setattr(authz, "assert_file_source_access", _deny)

    canvas = {
        "nodes": [
            {"type": "file_source", "file_source_id": "fs-1"},
            {"type": "file_source", "file_source_id": "fs-2"},
        ]
    }

    with pytest.raises(HTTPException) as exc:
        assert_canvas_resources_access(canvas, current_user)

    assert exc.value.status_code == 403
    assert exc.value.detail == "denied:fs-1"


def test_assert_project_access_rejects_missing_project_id(current_user):
    with pytest.raises(HTTPException) as exc:
        assert_project_access("", current_user)

    assert exc.value.status_code == 400


def test_assert_project_visible_uses_visibility_check(monkeypatch, current_user):
    seen = {}

    import app.services.project_service as project_service
    import app.shared.authz_helpers as authz

    monkeypatch.setattr(project_service.ProjectService, "get_project_metadata", lambda self, project_id: {"id": project_id, "group_id": "grp-1"})

    def _assert_visible(meta, table_name, user, label):
        seen["meta"] = meta
        seen["table_name"] = table_name
        seen["user_id"] = user.user_id
        seen["label"] = label

    monkeypatch.setattr(authz, "assert_resource_visible", _assert_visible)

    meta = assert_project_visible("proj-1", current_user)

    assert meta["id"] == "proj-1"
    assert seen["table_name"] == "projects"
    assert seen["user_id"] == "u-1"


def test_assert_file_source_access_rejects_invisible_resource(monkeypatch, current_user):
    import app.db.db_service as db_service
    import app.shared.authz_helpers as authz

    monkeypatch.setattr(db_service, "get_file_source", lambda file_source_id: {"id": file_source_id, "group_id": "grp-2"})

    def _deny(*args, **kwargs):
        raise HTTPException(status_code=403, detail="Accesso negato")

    monkeypatch.setattr(authz, "assert_resource_visible", _deny)

    with pytest.raises(HTTPException) as exc:
        assert_file_source_access("fs-1", current_user)

    assert exc.value.status_code == 403
