"""Integration: project_source cross-tenant deve essere bloccato a runtime."""

from __future__ import annotations

from app.core.auth.auth import hash_password
from app.db.config_db_service import create_group, create_user, delete_group_cascade

from tests2.functional.harness.http import http_json_response, poll_json_until
from tests2.integration.harness.db import make_it_id
from tests2.integration.harness.mysql import seeded_mysql_table
from tests2.integration.harness.projects import managed_project
from tests2.integration.test_mysql_connection_and_estrai_preview import (
    _create_mysql_connection,
    _delete_connection,
    _estrai_mysql_canvas,
)


def _arricchisci_project_source_canvas(source_project_id: str) -> dict:
    return {
        "nodes": [
            {
                "id": "src-1",
                "type": "project_source",
                "position": {"x": 100, "y": 100},
                "source_project_id": source_project_id,
                "columns": [],
            },
            {
                "id": "result-1",
                "type": "result",
                "position": {"x": 350, "y": 100},
                "fileName": "project_source_cross_tenant",
                "format": "csv",
                "columns": [],
            },
        ],
        "edges": [
            {"id": "edge-1", "source": "src-1", "target": "result-1", "join_type": "output"}
        ],
    }


def _seed_secondary_admin() -> dict:
    group_id = make_it_id("grp")
    user_id = make_it_id("user")
    username = make_it_id("usernm")
    password = "IntegrationPass123!"
    create_group(group_id, f"Integration {group_id}", "tests2 integration secondary")
    create_user(
        user_id=user_id,
        username=username,
        password_hash=hash_password(password),
        group_id=group_id,
        role="admin",
        email=f"{username}@example.test",
    )
    return {
        "group_id": group_id,
        "user_id": user_id,
        "username": username,
        "password": password,
    }


def test_project_source_cross_tenant_execute_fails_closed(
    integration_celery_worker,
    integration_backend_server,
    integration_auth_headers,
):
    _ = integration_celery_worker
    secondary = _seed_secondary_admin()
    connection = _create_mysql_connection(
        integration_backend_server["base_url"],
        integration_auth_headers,
    )
    try:
        with seeded_mysql_table() as seeded:
            with managed_project(
                base_url=integration_backend_server["base_url"],
                auth_headers=integration_auth_headers,
                name="Integration Upstream ESTRAI CrossTenant",
                project_type="estrai",
                canvas=_estrai_mysql_canvas(seeded["table_name"]),
                default_connection_id=connection["id"],
            ) as upstream:
                upstream_project_id = upstream["metadata"]["id"]

                status, upstream_task, _ = http_json_response(
                    f"{integration_backend_server['base_url']}/api/projects/{upstream_project_id}/execute",
                    method="POST",
                    payload={"force_recalculate": False},
                    headers=integration_auth_headers,
                    timeout=10.0,
                )
                assert status == 200
                upstream_task_id = upstream_task["task_id"]

                upstream_status = poll_json_until(
                    f"{integration_backend_server['base_url']}/api/tasks/{upstream_task_id}",
                    headers=integration_auth_headers,
                    timeout=120.0,
                    interval=2.0,
                    predicate=lambda body: isinstance(body, dict)
                    and body.get("status") in ("success", "failure"),
                )
                assert upstream_status["status"] == "success"
                assert upstream_status["result"]["success"] is True

                login_status, login_body, _ = http_json_response(
                    f"{integration_backend_server['base_url']}/api/auth/login",
                    method="POST",
                    payload={
                        "username": secondary["username"],
                        "password": secondary["password"],
                    },
                    timeout=10.0,
                )
                assert login_status == 200
                secondary_headers = {"Authorization": f"Bearer {login_body['access_token']}"}

                with managed_project(
                    base_url=integration_backend_server["base_url"],
                    auth_headers=secondary_headers,
                    name="Integration Downstream CrossTenant",
                    project_type="arricchisci",
                    canvas=_arricchisci_project_source_canvas(upstream_project_id),
                ) as downstream:
                    downstream_project_id = downstream["metadata"]["id"]

                    status, downstream_task, _ = http_json_response(
                        f"{integration_backend_server['base_url']}/api/projects/{downstream_project_id}/execute",
                        method="POST",
                        payload={"force_recalculate": False},
                        headers=secondary_headers,
                        timeout=10.0,
                    )
                    assert status == 200
                    downstream_task_id = downstream_task["task_id"]

                    downstream_status = poll_json_until(
                        f"{integration_backend_server['base_url']}/api/tasks/{downstream_task_id}",
                        headers=secondary_headers,
                        timeout=120.0,
                        interval=2.0,
                        predicate=lambda body: isinstance(body, dict)
                        and body.get("status") in ("success", "failure"),
                    )
                    assert downstream_status["status"] == "failure"
                    combined = (
                        str(downstream_status.get("error", "")) + " "
                        + str(downstream_status.get("result", "")) + " "
                        + str(downstream_status)
                    ).lower()
                    assert "grupp" in combined or "tenant" in combined or "non trovato" in combined or "forbidden" in combined
    finally:
        _delete_connection(
            integration_backend_server["base_url"],
            integration_auth_headers,
            connection["id"],
        )
        delete_group_cascade(secondary["group_id"])
