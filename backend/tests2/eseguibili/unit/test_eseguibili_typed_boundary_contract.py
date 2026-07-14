from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from app.core.eseguibili import service
from app.core.eseguibili.db import EseguibileRow
from app.core.eseguibili.models import (
    AnalyzeFileUploadResult,
    EseguibileExecutionResult,
    ExecuteRequest,
)
from app.shared.contracts.auth import CurrentUser


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.eseguibili]


def _row() -> EseguibileRow:
    return EseguibileRow(
        id="exec-1",
        name="Demo",
        description="",
        canvas={
            "nodes": [
                {"id": "source", "type": "file_source", "data": {}},
                {"id": "result", "type": "result", "data": {}},
            ],
            "edges": [],
        },
        expected_columns=[{"name": "customer_id", "required": True}],
        default_output_format="xlsx",
        group_id="grp-1",
        created_by="u-1",
        created_at="2026-05-09T00:00:00Z",
        updated_at="2026-05-09T00:00:00Z",
    )


def test_analyze_file_upload_returns_typed_internal_payload(monkeypatch):
    """Post-Phase5: analyze_file_upload accetta bytes (`file_content`)
    invece di path. Il dispatch avviene via `_load_dataframe_from_bytes`
    + `format_hint` derivato dal filename."""
    from app.core.dataframes import frame_io

    monkeypatch.setattr(
        frame_io,
        "_load_dataframe_from_bytes",
        lambda data, *, format_hint, sheet=None, layer=None: pd.DataFrame(
            {
                "customer_id": [1, 2],
                "name": ["Ada", None],
            }
        ),
    )

    result = service.analyze_file_upload(_row(), b"customer_id,name\n1,Ada\n2,\n", "upload.csv")

    assert isinstance(result, AnalyzeFileUploadResult)
    api_payload = result.to_api_dict()
    assert "file_path" not in api_payload
    assert api_payload["row_count"] == 2
    assert api_payload["auto_mapping"] == {"customer_id": "customer_id"}


def test_execute_returns_typed_internal_payload(monkeypatch):
    from app.core.files.service import file_source_service
    from app.core.dataframes import frame_io
    from app.core.orchestrator.task_service import task_service

    monkeypatch.setattr(
        frame_io,
        "load_dataframe_from_file",
        lambda path: pd.DataFrame({"source_customer_id": [1, 2]}),
    )
    monkeypatch.setattr(
        file_source_service,
        "create_from_upload",
        lambda **kwargs: SimpleNamespace(id="fs-temp-1"),
    )
    monkeypatch.setattr(
        task_service,
        "create_enrichment_task",
        lambda **kwargs: SimpleNamespace(task_id="task-1"),
    )

    result = service.execute(
        _row(),
        [{"chain_column": "customer_id", "file_column": "source_customer_id"}],
        file_path="/tmp/upload.csv",
    )

    assert isinstance(result, EseguibileExecutionResult)
    assert result.task_id == "task-1"
    assert result.to_api_dict() == {
        "async": True,
        "task_id": "task-1",
        "project_id": "exec-1",
        "temp_file_source_id": "fs-temp-1",
        "message": "Esecuzione avviata. Usa GET /api/tasks/{task_id} per il progresso.",
    }


def test_execute_hydrates_upload_from_file_source_before_worker_handoff(monkeypatch):
    from app.core.files.service import file_source_service
    from app.core.dataframes import frame_io
    from app.core.orchestrator.task_service import task_service

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        file_source_service,
        "load_dataframe",
        lambda file_id, expected_group_id=None: (
            pd.DataFrame({"source_customer_id": [1, 2]}),
            None,
        ),
    )
    monkeypatch.setattr(
        service,
        "load_upload_metadata",
        lambda file_id: {"filename": "upload.csv", "group_id": "grp-user"},
    )
    monkeypatch.setattr(
        file_source_service,
        "create_from_upload",
        lambda **kwargs: SimpleNamespace(id="fs-temp-1"),
    )

    def fake_create_enrichment_task(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(task_id="task-1")

    monkeypatch.setattr(task_service, "create_enrichment_task", fake_create_enrichment_task)

    result = service.execute(
        _row(),
        [{"chain_column": "customer_id", "file_column": "source_customer_id"}],
        file_source_id="fs-upload-1",
        upload_group_id="grp-user",
    )

    assert result.task_id == "task-1"
    assert captured["cleanup_file_source_ids"] == ["fs-temp-1"]
    assert captured["is_eseguibile"] is True
    canvas = captured["canvas"]
    source = canvas["nodes"][0]
    assert source["file_source_id"] == "fs-temp-1"
    assert source["data"]["file_source_id"] == "fs-temp-1"
    assert "file_path" not in str(captured)
    assert "fs-upload-1" not in str(captured)


def test_execute_router_serializes_typed_service_payload(monkeypatch):
    from app.core.eseguibili import router

    saved_owner: dict[str, str] = {}
    monkeypatch.setattr(router.eseguibili_db, "get_eseguibile", lambda eid: _row())
    monkeypatch.setattr(router, "_check_visible", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        router,
        "_load_upload_meta",
        lambda file_id: {
            "file_source_id": "fs-upload-1",
            "filename": "upload.csv",
            "eseguibile_id": "exec-1",
            "user_id": "u-1",
            "group_id": "grp-1",
        },
    )
    monkeypatch.setattr(router, "_delete_upload_meta", lambda file_id: None)
    monkeypatch.setattr(
        router,
        "_save_task_owner",
        lambda task_id, eseguibile_id, user_id, group_id: saved_owner.update(
            {
                "task_id": task_id,
                "eseguibile_id": eseguibile_id,
                "user_id": user_id,
                "group_id": group_id,
            }
        ),
    )
    monkeypatch.setattr(
        router.eseguibili_service,
        "execute",
        lambda **kwargs: EseguibileExecutionResult(
            **{
                "async": True,
                "task_id": "task-1",
                "project_id": "exec-1",
                "temp_file_source_id": "fs-temp-1",
                "message": "started",
            }
        ),
    )

    out = router.execute_eseguibile(
        "exec-1",
        ExecuteRequest(file_id="file-1", column_mapping=[]),
        CurrentUser(
            user_id="u-1",
            username="tester",
            group_id="grp-1",
            role="admin",
        ),
    )

    assert out == {
        "async": True,
        "task_id": "task-1",
        "project_id": "exec-1",
        "temp_file_source_id": "fs-temp-1",
        "message": "started",
    }
    assert saved_owner == {
        "task_id": "task-1",
        "eseguibile_id": "exec-1",
        "user_id": "u-1",
        "group_id": "grp-1",
    }


def test_execute_router_passes_file_source_id_not_local_path(monkeypatch):
    from app.core.eseguibili import router

    captured: dict[str, object] = {}
    monkeypatch.setattr(router.eseguibili_db, "get_eseguibile", lambda eid: _row())
    monkeypatch.setattr(router, "_check_visible", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        router,
        "_load_upload_meta",
        lambda file_id: {
            "file_source_id": "fs-upload-1",
            "filename": "upload.csv",
            "eseguibile_id": "exec-1",
            "user_id": "u-1",
            "group_id": "grp-user",
        },
    )
    monkeypatch.setattr(router, "_delete_upload_meta", lambda file_id: None)
    monkeypatch.setattr(router, "_save_task_owner", lambda *args, **kwargs: None)

    def fake_execute(**kwargs):
        captured.update(kwargs)
        return EseguibileExecutionResult(
            **{
                "async": True,
                "task_id": "task-1",
                "project_id": "exec-1",
                "temp_file_source_id": "fs-temp-1",
                "message": "started",
            }
        )

    monkeypatch.setattr(router.eseguibili_service, "execute", fake_execute)

    router.execute_eseguibile(
        "exec-1",
        ExecuteRequest(file_id="fs-upload-1", column_mapping=[]),
        CurrentUser(
            user_id="u-1",
            username="tester",
            group_id="grp-user",
            role="admin",
        ),
    )

    assert captured["file_source_id"] == "fs-upload-1"
    assert captured["upload_group_id"] == "grp-user"
    assert "file_path" not in captured
