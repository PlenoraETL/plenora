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
    from app.core.files import dataframe_io

    monkeypatch.setattr(
        dataframe_io,
        "load_dataframe_from_file",
        lambda path: pd.DataFrame(
            {
                "customer_id": [1, 2],
                "name": ["Ada", None],
            }
        ),
    )

    result = service.analyze_file_upload(_row(), "/tmp/upload.csv", "upload.csv")

    assert isinstance(result, AnalyzeFileUploadResult)
    assert result.file_path == "/tmp/upload.csv"
    api_payload = result.to_api_dict()
    assert "file_path" not in api_payload
    assert api_payload["row_count"] == 2
    assert api_payload["auto_mapping"] == {"customer_id": "customer_id"}


def test_execute_returns_typed_internal_payload(monkeypatch):
    from app.core.files.service import file_source_service
    from app.core.files import dataframe_io
    from app.core.orchestrator.task_service import task_service

    monkeypatch.setattr(
        dataframe_io,
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
        "/tmp/upload.csv",
        [{"chain_column": "customer_id", "file_column": "source_customer_id"}],
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


def test_execute_router_serializes_typed_service_payload(monkeypatch):
    from app.core.eseguibili import router

    saved_owner: dict[str, str] = {}
    monkeypatch.setattr(router.eseguibili_db, "get_eseguibile", lambda eid: _row())
    monkeypatch.setattr(router, "_check_visible", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        router,
        "_load_upload_meta",
        lambda file_id: {
            "path": "/tmp/upload.csv",
            "filename": "upload.csv",
            "eseguibile_id": "exec-1",
            "user_id": "u-1",
        },
    )
    monkeypatch.setattr(router.os.path, "exists", lambda path: False)
    monkeypatch.setattr(router, "_delete_upload_meta", lambda file_id: None)
    monkeypatch.setattr(
        router,
        "_save_task_owner",
        lambda task_id, eseguibile_id, user_id: saved_owner.update(
            {
                "task_id": task_id,
                "eseguibile_id": eseguibile_id,
                "user_id": user_id,
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
    }
