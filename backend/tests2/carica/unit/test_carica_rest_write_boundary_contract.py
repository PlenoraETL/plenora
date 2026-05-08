from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from app.core.carica.rest_write_boundary import execute_carica_rest_write
from app.core.features.rest_sink import (
    RestSinkBatchMode,
    RestSinkBodyField,
    RestSinkBodyMapping,
    RestSinkConfig,
    RestSinkHeader,
    RestSinkHttpResponse,
    RestSinkMethod,
)


@dataclass
class CapturedRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    json: object
    timeout: float


class FakeHttpClient:
    def __init__(
        self,
        responses: list[RestSinkHttpResponse] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.responses = responses or [RestSinkHttpResponse(status_code=201)]
        self.error = error
        self.requests: list[CapturedRequest] = []

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
            CapturedRequest(
                method=method,
                url=url,
                headers=dict(headers),
                json=json,
                timeout=timeout,
            )
        )
        if self.error is not None:
            raise self.error
        return self.responses.pop(0)


def test_carica_rest_boundary_writes_dataframe_with_injected_client() -> None:
    df = pd.DataFrame(
        [
            {"id": 1, "name": "Alpha"},
            {"id": 2, "name": "Beta"},
            {"id": 3, "name": "Gamma"},
        ]
    )
    client = FakeHttpClient(
        responses=[
            RestSinkHttpResponse(status_code=201),
            RestSinkHttpResponse(status_code=201),
        ]
    )
    config = RestSinkConfig(
        url="https://api.example.test/features",
        method=RestSinkMethod.POST,
        batch_mode=RestSinkBatchMode.BATCH,
        batch_size=2,
    )

    outcome = execute_carica_rest_write(df=df, config=config, http_client=client)
    result = outcome.to_task_result()

    assert result["status"] == "completed"
    assert result["success"] is True
    assert result["rows_written"] == 3
    assert result["rows_failed"] == 0
    assert result["total_rows"] == 3
    assert result["message"] == "Scrittura REST completata: 3 righe"
    assert result["batches"] == [
        {"success": True, "status_code": 201, "sent_count": 2, "error": ""},
        {"success": True, "status_code": 201, "sent_count": 1, "error": ""},
    ]
    assert len(client.requests) == 2
    assert client.requests[0].json == {
        "records": [
            {"id": 1, "name": "Alpha"},
            {"id": 2, "name": "Beta"},
        ]
    }


def test_carica_rest_boundary_reports_partial_failures() -> None:
    df = pd.DataFrame(
        [
            {"id": 1},
            {"id": 2},
            {"id": 3},
        ]
    )
    client = FakeHttpClient(
        responses=[
            RestSinkHttpResponse(status_code=201),
            RestSinkHttpResponse(status_code=500, text="upstream rejected row"),
        ]
    )
    config = RestSinkConfig(
        url="https://api.example.test/features",
        batch_mode=RestSinkBatchMode.BATCH,
        batch_size=2,
    )

    result = execute_carica_rest_write(
        df=df,
        config=config,
        http_client=client,
    ).to_task_result()

    assert result["status"] == "partial"
    assert result["success"] is False
    assert result["rows_written"] == 2
    assert result["rows_failed"] == 1
    assert result["total_rows"] == 3
    assert result["error"] == "carica_rest_write_failed"
    assert result["error_message"] == (
        "REST sink request failed with status 500: upstream rejected row"
    )


def test_carica_rest_boundary_does_not_leak_secret_headers_on_error() -> None:
    secret = "Bearer secret-token"
    df = pd.DataFrame([{"id": 1}])
    client = FakeHttpClient(
        responses=[
            RestSinkHttpResponse(
                status_code=401,
                text="bad token Bearer secret-token",
            )
        ]
    )
    config = RestSinkConfig(
        url="https://api.example.test/features",
        headers=(RestSinkHeader("Authorization", secret, secret=True),),
    )

    result = execute_carica_rest_write(
        df=df,
        config=config,
        http_client=client,
    ).to_task_result()

    assert result["success"] is False
    assert "secret-token" not in repr(result)
    assert "Bearer secret-token" not in repr(result)
    assert "***REDACTED***" in result["error_message"]
    assert client.requests[0].headers["Authorization"] == secret


def test_carica_rest_boundary_uses_per_record_mode() -> None:
    df = pd.DataFrame([{"id": 1}, {"id": 2}])
    client = FakeHttpClient(
        responses=[
            RestSinkHttpResponse(status_code=204),
            RestSinkHttpResponse(status_code=204),
        ]
    )
    config = RestSinkConfig(
        url="https://api.example.test/features",
        method=RestSinkMethod.PATCH,
        batch_mode=RestSinkBatchMode.PER_RECORD,
        success_statuses=(204,),
    )

    result = execute_carica_rest_write(
        df=df,
        config=config,
        http_client=client,
    ).to_task_result()

    assert result["success"] is True
    assert result["rows_written"] == 2
    assert [request.json for request in client.requests] == [{"id": 1}, {"id": 2}]
    assert {request.method for request in client.requests} == {"PATCH"}


def test_carica_rest_boundary_redacts_secret_from_transport_exception() -> None:
    secret = "Bearer secret-token"
    df = pd.DataFrame([{"id": 1}])
    client = FakeHttpClient(error=TimeoutError("timeout for Bearer secret-token"))
    config = RestSinkConfig(
        url="https://api.example.test/features",
        headers=(RestSinkHeader("Authorization", secret, secret=True),),
    )

    result = execute_carica_rest_write(
        df=df,
        config=config,
        http_client=client,
    ).to_task_result()

    assert result["status"] == "write_error"
    assert result["success"] is False
    assert result["rows_written"] == 0
    assert result["rows_failed"] == 1
    assert "secret-token" not in repr(result)
    assert "Bearer secret-token" not in repr(result)
    assert "***REDACTED***" in result["error_message"]


def test_carica_rest_boundary_required_body_mapping_failure_sends_no_request() -> None:
    df = pd.DataFrame([{"id": 1, "name": "Alpha"}])
    client = FakeHttpClient()
    config = RestSinkConfig(
        url="https://api.example.test/features",
        body_mapping=RestSinkBodyMapping(
            fields=(
                RestSinkBodyField(
                    source="external_id",
                    target="external_id",
                    required=True,
                ),
            ),
            include_unmapped=False,
        ),
    )

    result = execute_carica_rest_write(
        df=df,
        config=config,
        http_client=client,
    ).to_task_result()

    assert result["status"] == "write_error"
    assert result["success"] is False
    assert result["rows_written"] == 0
    assert result["rows_failed"] == 1
    assert result["error_message"] == "REST sink missing required field: external_id"
    assert result["batches"] == [
        {
            "success": False,
            "status_code": None,
            "sent_count": 0,
            "error": "REST sink missing required field: external_id",
        }
    ]
    assert client.requests == []


def test_carica_rest_boundary_rate_limit_failure_keeps_successful_batches() -> None:
    df = pd.DataFrame([{"id": 1}, {"id": 2}, {"id": 3}])
    client = FakeHttpClient(
        responses=[
            RestSinkHttpResponse(status_code=201),
            RestSinkHttpResponse(status_code=429, text="<html>too many requests</html>"),
        ]
    )
    config = RestSinkConfig(
        url="https://api.example.test/features",
        batch_mode=RestSinkBatchMode.BATCH,
        batch_size=2,
    )

    result = execute_carica_rest_write(
        df=df,
        config=config,
        http_client=client,
    ).to_task_result()

    assert result["status"] == "partial"
    assert result["success"] is False
    assert result["rows_written"] == 2
    assert result["rows_failed"] == 1
    assert result["error_message"] == (
        "REST sink request failed with status 429: <html>too many requests</html>"
    )
    assert result["batches"][1] == {
        "success": False,
        "status_code": 429,
        "sent_count": 0,
        "error": "REST sink request failed with status 429: <html>too many requests</html>",
    }
