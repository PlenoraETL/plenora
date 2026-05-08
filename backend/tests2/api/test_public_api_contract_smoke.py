"""Public API JSON contract smoke for typed backend internals."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shared.contracts.auth import CurrentUser


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def _user(role: str = "admin") -> CurrentUser:
    return CurrentUser(
        user_id="u-1",
        username="tester",
        group_id="grp-1",
        role=role,
    )


def test_orchestrator_task_status_public_shape_survives_typed_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.auth.dependencies import get_current_user
    from app.core.orchestrator import router as router_mod
    from app.core.orchestrator.router import tasks_router
    from app.core.orchestrator.task_service import (
        TaskInfo,
        TaskResultPayload,
        TaskStatus,
        TaskType,
    )

    payload = TaskResultPayload.from_celery_meta(
        {
            "success": True,
            "project_id": "proj-1",
            "result_id": "res-1",
            "output_file": "out.csv",
            "rows_count": 2,
        }
    )
    task_info = TaskInfo(
        task_id="task-1",
        task_type=TaskType.EXECUTE,
        project_id="proj-1",
        status=TaskStatus.SUCCESS,
        progress=100,
        message="done",
        result=payload,
    )
    monkeypatch.setattr(router_mod, "_assert_task_group", lambda *a, **kw: None)
    monkeypatch.setattr(
        "app.core.orchestrator.task_service.task_service.get_task_status",
        lambda task_id: task_info,
    )

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: _user()
    app.include_router(tasks_router, prefix="/api")

    response = TestClient(app, raise_server_exceptions=False).get("/api/tasks/task-1")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "task_id",
        "task_type",
        "project_id",
        "status",
        "progress",
        "message",
        "result",
        "error",
        "created_at",
        "started_at",
        "completed_at",
    }
    assert body["result"] == {
        "success": True,
        "project_id": "proj-1",
        "result_id": "res-1",
        "output_file": "out.csv",
        "rows_count": 2,
    }


def test_admin_system_status_public_shape_survives_typed_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.admin_system_status import service
    from app.core.admin_system_status.router import router
    from app.core.auth.dependencies import require_superadmin

    payload = {
        "generated_at": "2026-05-08T00:00:00Z",
        "backup": {"status": "ok", "message": "ok"},
        "verify_restore": {"status": "unknown", "message": "none"},
        "schedules": {"status": "ok", "message": "ok"},
        "workers": {"status": "ok", "message": "ok"},
        "host_resources": {"status": "ok", "message": "ok"},
        "db_pool": {"status": "ok", "message": "ok"},
        "redis": {"status": "ok", "message": "ok"},
        "db_schema": {"status": "ok", "message": "ok"},
        "jwt_blacklist": {"status": "ok", "message": "ok"},
        "tickets": {"status": "warn", "message": "open"},
    }
    monkeypatch.setattr(
        service,
        "get_system_status",
        lambda: SimpleNamespace(to_api_dict=lambda: payload),
    )

    app = FastAPI()
    app.dependency_overrides[require_superadmin] = lambda: _user("superadmin")
    app.include_router(router, prefix="/api")

    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/admin/system-status"
    )

    assert response.status_code == 200
    assert set(response.json()) == set(payload)


def test_tickets_public_shapes_survive_typed_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.auth.dependencies import get_current_user
    from app.core.tickets import service as svc
    from app.core.tickets.router import router
    from app.db import tickets_db_service as tdb

    ticket = tdb.TicketRow(
        id="t-1",
        kind="user_support",
        title="Need help",
        description="details",
        severity="medium",
        status="open",
        category="other",
        created_by="u-1",
        group_id="grp-1",
        created_at="2026-05-08T00:00:00Z",
        updated_at="2026-05-08T00:00:00Z",
    )
    comment = tdb.CommentRow(
        id="c-1",
        ticket_id="t-1",
        author_id="u-1",
        body="note",
        created_at="2026-05-08T00:00:01Z",
        author_username="tester",
    )
    attachment = tdb.AttachmentRow(
        id="a-1",
        ticket_id="t-1",
        file_source_id="fs-1",
        uploaded_by="u-1",
        uploaded_at="2026-05-08T00:00:02Z",
        file_name="log",
    )
    monkeypatch.setattr(svc, "list_tickets_for", lambda **kw: [ticket])
    monkeypatch.setattr(
        svc,
        "get_ticket_with_details",
        lambda ticket_id, actor: svc.TicketDetails(
            ticket=ticket,
            comments=[comment],
            attachments=[attachment],
        ),
    )

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: _user()
    app.include_router(router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=False)

    list_body = client.get("/api/tickets").json()
    assert set(list_body) == {"tickets"}
    assert set(list_body["tickets"][0]) == {
        "id",
        "kind",
        "title",
        "description",
        "severity",
        "status",
        "category",
        "created_by",
        "group_id",
        "created_at",
        "updated_at",
        "assignee",
        "resolved_at",
        "deleted_at",
    }

    detail_body = client.get("/api/tickets/t-1").json()
    assert set(detail_body) == {"ticket", "comments", "attachments"}
    assert detail_body["comments"][0]["author_username"] == "tester"
    assert detail_body["attachments"][0]["file_source_id"] == "fs-1"


def test_eseguibili_public_shapes_survive_typed_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.auth.dependencies import get_current_user
    from app.core.eseguibili import db as eseguibili_db
    from app.core.eseguibili import router as router_mod
    from app.core.eseguibili.router import router

    row = eseguibili_db.EseguibileSummaryRow(
        id="exec-1",
        name="Runnable",
        description="",
        default_output_format="xlsx",
        group_id="grp-1",
        created_by="u-1",
        created_at="2026-05-08T00:00:00Z",
        updated_at="2026-05-08T00:00:00Z",
        expected_columns_count=3,
    )
    monkeypatch.setattr(eseguibili_db, "list_eseguibili", lambda group_id=None: [row])
    monkeypatch.setattr(router_mod, "visible_resource_ids", lambda *a, **kw: {"exec-1"})

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: _user()
    app.include_router(router, prefix="/api")

    response = TestClient(app, raise_server_exceptions=False).get("/api/eseguibili")

    assert response.status_code == 200
    assert set(response.json()[0]) == {
        "id",
        "name",
        "description",
        "default_output_format",
        "icon",
        "group_id",
        "created_by",
        "created_at",
        "updated_at",
        "expected_columns_count",
        "can_edit",
    }


def test_connections_public_shape_survives_typed_service_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.auth.dependencies import require_not_esecutore
    from app.core.connections import router as router_mod
    from app.core.connections.models import ConnectionList, DatabaseConnection
    from app.core.connections.router import router

    connection = DatabaseConnection(
        id="conn-1",
        name="Postgres",
        description="",
        type="postgres",
        host="localhost",
        port=5432,
        database="db",
        db_schema="public",
        username="usr",
        password="secret",
        group_id="grp-1",
        created_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
        created_by="u-1",
    )
    service = MagicMock()
    service.list_connections.return_value = ConnectionList(connections=[connection])
    monkeypatch.setattr(router_mod, "_connection_service", service)
    monkeypatch.setattr(router_mod, "visible_resource_ids", lambda *a, **kw: {"conn-1"})

    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: _user()
    app.include_router(router, prefix="/api")

    response = TestClient(app, raise_server_exceptions=False).get("/api/connections")

    assert response.status_code == 200
    assert set(response.json()) == {"connections"}
    assert set(response.json()["connections"][0]) == {
        "id",
        "name",
        "description",
        "type",
        "host",
        "port",
        "database",
        "db_schema",
        "username",
        "password",
        "group_id",
        "created_at",
        "updated_at",
        "created_by",
        "can_edit",
    }


def test_projects_public_shape_survives_typed_service_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.auth.dependencies import require_not_esecutore
    from app.core.projects import router as router_mod
    from app.core.projects.models import ProjectMetadata
    from app.core.projects.router import router

    project = ProjectMetadata(
        id="proj-1",
        name="Pipeline",
        description="",
        project_type="estrai",
        default_connection_id="conn-1",
        group_id="grp-1",
        created_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )
    service = MagicMock()
    service.list_projects.return_value = SimpleNamespace(projects=[project])
    monkeypatch.setattr(router_mod, "_project_service", service)
    monkeypatch.setattr(router_mod, "group_filter", lambda current_user: "grp-1")

    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: _user()
    app.include_router(router, prefix="/api")

    response = TestClient(app, raise_server_exceptions=False).get("/api/projects")

    assert response.status_code == 200
    assert set(response.json()) == {"projects"}
    assert set(response.json()["projects"][0]) == {
        "id",
        "name",
        "description",
        "project_type",
        "default_connection_id",
        "group_id",
        "created_at",
        "updated_at",
    }


def test_results_public_legacy_shape_survives_typed_row_bridge(monkeypatch) -> None:
    from app.core.results import db as results_db

    def _fetchone(sql: str, params: tuple[object, ...]) -> dict[str, object]:
        return {
            "id": "res-1",
            "project_id": "proj-1",
            "group_id": "grp-1",
            "filename": "out.csv",
            "format": "csv",
            "size_bytes": 12,
            "rows_count": 2,
            "created_at": "2026-05-08T00:00:00Z",
            "storage_key": "results/proj-1/res-1.bin",
        }

    monkeypatch.setattr(results_db, "fetchone", _fetchone)

    row = results_db.get_result_row("res-1")
    assert row is not None
    assert not hasattr(row, "to_legacy_dict")
    assert results_db.get_result("res-1") == {
        "id": "res-1",
        "project_id": "proj-1",
        "group_id": "grp-1",
        "filename": "out.csv",
        "format": "csv",
        "size_bytes": 12,
        "rows_count": 2,
        "created_at": "2026-05-08T00:00:00Z",
        "storage_key": "results/proj-1/res-1.bin",
    }
