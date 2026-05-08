from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pytest

pd = pytest.importorskip("pandas")

from app.core.features.rest_sink import RestSinkHttpResponse


@dataclass
class CapturedRestRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    json: object
    timeout: float


class FakeRestHttpClient:
    def __init__(self) -> None:
        self.requests: list[CapturedRestRequest] = []

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json: object,
        timeout: float,
    ) -> RestSinkHttpResponse:
        self.requests.append(
            CapturedRestRequest(
                method=method,
                url=url,
                headers=dict(headers),
                json=json,
                timeout=timeout,
            )
        )
        return RestSinkHttpResponse(status_code=201, body={"accepted": True})


def _rest_node_data() -> dict[str, object]:
    return {
        "dest_type": "rest",
        "rest": {
            "url": "https://api.example.test/import/features",
            "method": "POST",
            "headers": [
                {"name": "Authorization", "value": "Bearer secret-token", "secret": True},
                {"name": "X-Client", "value": "plenora"},
            ],
            "body_mapping": {
                "fields": [
                    {"source": "id", "target": "external_id", "required": True},
                    {"source": "name", "target": "label"},
                ],
                "include_unmapped": False,
                "batch_records_key": "records",
            },
            "batch_mode": "batch",
            "batch_size": 2,
            "timeout_seconds": 5,
            "success_statuses": [201],
        },
    }


def test_carica_rest_contract_writes_upstream_dataframe_with_fake_http_client() -> None:
    from app.core.carica.rest_config_parser import parse_carica_rest_config
    from app.core.carica.rest_write_boundary import execute_carica_rest_write

    df = pd.DataFrame(
        [
            {"id": 10, "name": "Alpha", "ignored": "drop-me"},
            {"id": 20, "name": "Beta", "ignored": "drop-me-too"},
        ]
    )
    fake_client = FakeRestHttpClient()

    parsed = parse_carica_rest_config(_rest_node_data())
    result = execute_carica_rest_write(
        df=df,
        config=parsed.sink_config,
        http_client=fake_client,
    ).to_task_result()

    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["rows_written"] == 2
    assert result["rows_failed"] == 0
    assert result["total_rows"] == 2
    assert len(fake_client.requests) == 1
    assert fake_client.requests[0].method == "POST"
    assert fake_client.requests[0].url == "https://api.example.test/import/features"
    assert fake_client.requests[0].headers["Authorization"] == "Bearer secret-token"
    assert fake_client.requests[0].json == {
        "records": [
            {"external_id": 10, "label": "Alpha"},
            {"external_id": 20, "label": "Beta"},
        ]
    }
    assert "secret-token" not in repr(result)
    assert "Bearer secret-token" not in repr(result)


def test_carica_rest_contract_counts_partial_failures_and_redacts_secrets() -> None:
    from app.core.carica.rest_config_parser import parse_carica_rest_config
    from app.core.carica.rest_write_boundary import execute_carica_rest_write

    class PartiallyFailingClient(FakeRestHttpClient):
        def request(
            self,
            *,
            method: str,
            url: str,
            headers: Mapping[str, str],
            json: object,
            timeout: float,
        ) -> RestSinkHttpResponse:
            self.requests.append(
                CapturedRestRequest(method, url, dict(headers), json, timeout)
            )
            return RestSinkHttpResponse(
                status_code=500,
                text="upstream rejected Bearer secret-token",
            )

    df = pd.DataFrame([{"id": 10, "name": "Alpha"}, {"id": 20, "name": "Beta"}])
    fake_client = PartiallyFailingClient()

    parsed = parse_carica_rest_config(_rest_node_data())
    result = execute_carica_rest_write(
        df=df,
        config=parsed.sink_config,
        http_client=fake_client,
    ).to_task_result()

    assert result["success"] is False
    assert result["rows_written"] == 0
    assert result["rows_failed"] == 2
    assert result["total_rows"] == 2
    assert "secret-token" not in repr(result)
    assert "Bearer secret-token" not in repr(result)
