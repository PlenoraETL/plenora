"""Contracts for routing CARICA Celery writes to the REST boundary."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.core.features.rest_sink import RestSinkHttpResponse
from workers.tasks.carica_task import (
    _execute_rest_write,
    _select_carica_write_branch,
)


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


def test_carica_write_branch_selects_rest_without_stealing_existing_branches() -> None:
    assert _select_carica_write_branch({"dest_type": "rest"}) == "rest"
    assert _select_carica_write_branch({"dest_type": "db"}) == "db"
    assert _select_carica_write_branch({}) == "db"

    assert _select_carica_write_branch({"dest_type": "arcgis"}) == "arcgis"
    assert _select_carica_write_branch({"type": "carica_arcgis"}) == "arcgis"

    assert (
        _select_carica_write_branch(
            {"type": "datasource_node", "dest_type": "rest"}
        )
        == "datasource"
    )


def test_rest_write_branch_delegates_to_carica_rest_boundary(monkeypatch) -> None:
    df = pd.DataFrame([{"id": 1, "name": "A"}, {"id": 2, "name": "B"}])
    task = SimpleNamespace(update_state=MagicMock())
    node_data = {
        "dest_type": "rest",
        "rest": {
            "url": "https://api.example.test/items",
            "method": "POST",
            "headers": {"X-Client": "plenora"},
            "batch_size": 2,
            "success_statuses": [201],
        },
    }
    fake_client = SimpleNamespace(
        request=MagicMock(
            return_value=RestSinkHttpResponse(
                status_code=201,
                body={"accepted": True},
                text="",
            )
        )
    )

    monkeypatch.setattr(
        "workers.tasks.carica_task.create_default_rest_http_client",
        lambda: fake_client,
    )

    result = _execute_rest_write(
        task=task,
        df=df,
        carica_node_data=node_data,
        project_id="proj-1",
        task_id="task-1",
    )

    assert result["status"] == "completed"
    assert result["success"] is True
    assert result["rows_written"] == 2
    assert result["rows_failed"] == 0
    assert result["total_rows"] == 2
    fake_client.request.assert_called_once()
    assert fake_client.request.call_args.kwargs["url"] == "https://api.example.test/items"
    assert fake_client.request.call_args.kwargs["method"] == "POST"
    progress_messages = [
        call.kwargs["meta"]["message"]
        for call in task.update_state.call_args_list
    ]
    assert progress_messages == [
        "Preparazione scrittura REST...",
        "Scrittura REST...",
        "REST completato: 2 righe scritte",
    ]
