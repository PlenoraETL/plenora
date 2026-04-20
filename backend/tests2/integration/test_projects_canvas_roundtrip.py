"""Integration: creazione progetto + canvas + cleanup via API reale."""

from __future__ import annotations

from tests2.functional.harness.http import http_json_response
from tests2.integration.harness.projects import managed_project, update_canvas_via_api


def _minimal_estrai_canvas() -> dict:
    return {
        "nodes": [
            {
                "id": "src-1",
                "type": "source",
                "position": {"x": 20, "y": 100},
                "table_name": "DUMMY_TABLE",
                "columns": [],
                "filters": [],
            },
            {
                "id": "result-1",
                "type": "result",
                "position": {"x": 220, "y": 100},
                "data": {},
                "fileName": "integration_output",
                "format": "csv",
                "columns": [],
            },
        ],
        "edges": [
            {"id": "edge-1", "source": "src-1", "target": "result-1", "join_type": "output"},
        ],
    }


def test_project_create_with_canvas_roundtrip(
    integration_backend_server,
    integration_auth_headers,
):
    canvas = _minimal_estrai_canvas()
    with managed_project(
        base_url=integration_backend_server["base_url"],
        auth_headers=integration_auth_headers,
        name="Integration Canvas Project",
        project_type="estrai",
        canvas=canvas,
    ) as created:
        project_id = created["metadata"]["id"]
        assert created["metadata"]["project_type"] == "estrai"
        assert {n["id"] for n in created["canvas"]["nodes"]} == {"src-1", "result-1"}

        status, fetched, _ = http_json_response(
            f"{integration_backend_server['base_url']}/api/projects/{project_id}",
            headers=integration_auth_headers,
            timeout=30.0,
        )
        assert status == 200
        assert fetched["metadata"]["id"] == project_id
        assert {n["id"] for n in fetched["canvas"]["nodes"]} == {"src-1", "result-1"}


def test_project_canvas_update_roundtrip(
    integration_backend_server,
    integration_auth_headers,
):
    with managed_project(
        base_url=integration_backend_server["base_url"],
        auth_headers=integration_auth_headers,
        name="Integration Canvas Update",
        project_type="estrai",
    ) as created:
        project_id = created["metadata"]["id"]
        updated_canvas = _minimal_estrai_canvas()
        updated_canvas["nodes"][0]["fileName"] = "integration_updated"

        body = update_canvas_via_api(
            base_url=integration_backend_server["base_url"],
            auth_headers=integration_auth_headers,
            project_id=project_id,
            canvas=updated_canvas,
        )
        assert body["status"] == "ok"
        assert body["project_id"] == project_id

        status, fetched, _ = http_json_response(
            f"{integration_backend_server['base_url']}/api/projects/{project_id}",
            headers=integration_auth_headers,
            timeout=30.0,
        )
        assert status == 200
        assert fetched["canvas"]["nodes"][0]["fileName"] == "integration_updated"
