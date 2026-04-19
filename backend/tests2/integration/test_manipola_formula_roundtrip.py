"""Integration MANIPOLA: file_source -> transform formula -> result."""

from __future__ import annotations

from urllib.request import Request, urlopen

from tests2.functional.harness.http import http_json_response, poll_json_until
from tests2.integration.harness.file_sources import seed_csv_file_source
from tests2.integration.harness.projects import managed_project


def _manipola_formula_canvas(file_source_id: str) -> dict:
    return {
        "nodes": [
            {
                "id": "fs-1",
                "type": "file_source",
                "position": {"x": 100, "y": 100},
                "file_source_id": file_source_id,
                "columns": [],
            },
            {
                "id": "tr-1",
                "type": "transform",
                "position": {"x": 320, "y": 100},
                "transform_op": "filter",
                "transform_category": "base",
                "config": {
                    "column": "score",
                    "operator": ">",
                    "value": "15",
                },
                "data": {
                    "config": {
                        "column": "score",
                        "operator": ">",
                        "value": "15",
                    }
                },
            },
            {
                "id": "result-1",
                "type": "result",
                "position": {"x": 560, "y": 100},
                "fileName": "manipola_filter_integration",
                "format": "csv",
                "columns": [],
            },
        ],
        "edges": [
            # L'edge "fs-1 -> tr-1" NON usa join_type="input" perche' il
            # model Edge non accetta 'input' come literal (solo
            # left/inner/right/outer/output/carica/...). Omettere il campo
            # lascia il default ("left") che per un transform single-input
            # e' semanticamente corretto (feed-through).
            {"id": "edge-1", "source": "fs-1", "target": "tr-1"},
            {"id": "edge-2", "source": "tr-1", "target": "result-1", "join_type": "output"},
        ],
    }


def test_manipola_filter_execute_download_roundtrip(
    integration_celery_worker,
    integration_backend_server,
    integration_auth_headers,
    integration_user_seed,
):
    _ = integration_celery_worker
    file_source = seed_csv_file_source(
        group_id=integration_user_seed["group_id"],
        created_by=integration_user_seed["user_id"],
        rows=[
            {"city": "Rome", "country": "IT", "score": 10},
            {"city": "Milan", "country": "IT", "score": 20},
        ],
    )

    with managed_project(
        base_url=integration_backend_server["base_url"],
        auth_headers=integration_auth_headers,
        name="Integration Manipola Filter",
        project_type="manipola",
        canvas=_manipola_formula_canvas(file_source["id"]),
    ) as created:
        project_id = created["metadata"]["id"]
        # Regression guard: il POST /projects non deve emettere un warning
        # su canvas save, altrimenti il canvas non e' persistito e il
        # manipola executor legge nodes=[] edges=[] (cadiamo in
        # "Nessun progetto sorgente configurato" — il falso positivo
        # che ha portato a scoprire il bug 'join_type=input' non nel model).
        assert "warning" not in created, (
            f"create_project ha emesso warning: {created.get('warning')}"
        )
        status, task_created, _ = http_json_response(
            f"{integration_backend_server['base_url']}/api/projects/{project_id}/execute",
            method="POST",
            payload={"force_recalculate": False},
            headers=integration_auth_headers,
            timeout=10.0,
        )
        assert status == 200
        task_id = task_created["task_id"]

        final_status = poll_json_until(
            f"{integration_backend_server['base_url']}/api/tasks/{task_id}",
            headers=integration_auth_headers,
            timeout=120.0,
            interval=2.0,
            predicate=lambda body: isinstance(body, dict)
            and body.get("status") in ("success", "failure"),
        )
        assert final_status["status"] == "success"
        assert final_status["result"]["success"] is True
        assert final_status["result"]["result_id"]

        req = Request(
            f"{integration_backend_server['base_url']}/api/projects/{project_id}/download",
            headers=integration_auth_headers,
            method="GET",
        )
        with urlopen(req, timeout=10.0) as resp:
            payload = resp.read().decode("utf-8")
            assert resp.status == 200
            assert "Milan" in payload
            assert "20" in payload
            assert "Rome" not in payload
            assert "10" not in payload
