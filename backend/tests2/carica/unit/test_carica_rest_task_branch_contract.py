"""Contracts for routing CARICA Celery writes to the REST boundary."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.core.connections.rest_sink import RestSinkHttpResponse
from app.core.carica.dispatch import (
    execute_rest_write,
    select_carica_write_branch,
)


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


def test_carica_write_branch_selects_rest_without_stealing_existing_branches() -> None:
    assert select_carica_write_branch({"dest_type": "rest"}) == "rest"
    assert select_carica_write_branch({"dest_type": "db"}) == "db"
    assert select_carica_write_branch({}) == "db"

    assert select_carica_write_branch({"dest_type": "arcgis"}) == "arcgis"
    assert select_carica_write_branch({"type": "carica_arcgis"}) == "arcgis"

    assert (
        select_carica_write_branch(
            {"type": "datasource_node", "dest_type": "rest"}
        )
        == "datasource"
    )


def test_rest_write_branch_delegates_to_carica_rest_boundary(monkeypatch) -> None:
    df = pd.DataFrame([{"id": 1, "name": "A"}, {"id": 2, "name": "B"}])
    progress_messages: list[str] = []
    node_data = {
        "dest_type": "rest",
        "rest": {
            "url": "https://api.example.com/items",
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
        "app.core.carica.dispatch.create_default_rest_http_client",
        lambda: fake_client,
    )

    result = execute_rest_write(
        df=df,
        carica_node_data=node_data,
        project_id="proj-1",
        task_id="task-1",
        progress=lambda _p, m: progress_messages.append(m),
    )

    assert result["status"] == "completed"
    assert result["success"] is True
    assert result["rows_written"] == 2
    assert result["rows_failed"] == 0
    assert result["total_rows"] == 2
    fake_client.request.assert_called_once()
    assert fake_client.request.call_args.kwargs["url"] == "https://api.example.com/items"
    assert fake_client.request.call_args.kwargs["method"] == "POST"
    assert progress_messages == [
        "Preparazione scrittura REST...",
        "Scrittura REST...",
        "REST completato: 2 righe scritte",
    ]
