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


def _client(router, dependency, role: str = "admin") -> TestClient:
    app = FastAPI()
    app.dependency_overrides[dependency] = lambda: _user(role)
    app.include_router(router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


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

    response = _client(tasks_router, get_current_user).get("/api/tasks/task-1")

    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == "task-1"
    assert body["status"] == "success"
    assert body["result"]["result_id"] == "res-1"
    assert body["result"]["rows_count"] == 2


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

    response = _client(router, require_superadmin, "superadmin").get(
        "/api/admin/system-status"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["generated_at"] == payload["generated_at"]
    assert body["tickets"]["status"] == "warn"


def test_tickets_public_shapes_survive_typed_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.auth.dependencies import get_current_user
    from app.core.tickets import service as svc
    from app.core.tickets.router import router
    from app.core.tickets import db as tdb

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

    client = _client(router, get_current_user)

    list_response = client.get("/api/tickets")
    assert list_response.status_code == 200
    list_body = list_response.json()
    assert set(list_body) == {"tickets"}
    assert list_body["tickets"][0]["id"] == "t-1"

    detail_response = client.get("/api/tickets/t-1")
    assert detail_response.status_code == 200
    detail_body = detail_response.json()
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

    response = _client(router, get_current_user).get("/api/eseguibili")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["id"] == "exec-1"
    assert body[0]["can_edit"] is True


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

    response = _client(router, require_not_esecutore).get("/api/connections")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"connections"}
    assert body["connections"][0]["id"] == "conn-1"
    assert body["connections"][0]["can_edit"] is True


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

    response = _client(router, require_not_esecutore).get("/api/projects")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"projects"}
    assert body["projects"][0]["id"] == "proj-1"
    assert body["projects"][0]["project_type"] == "estrai"
