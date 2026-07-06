"""Contract tests for the Arricchisci async REST poller."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from requests.structures import CaseInsensitiveDict

from app.core.arricchisci.async_poller import (
    AsyncPollError,
    AsyncPoller,
    validate_async_config,
)
from app.core.arricchisci.http_executor import NormalizedResponse, RequestSpec


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


def _response(
    status: int,
    body: object,
    *,
    headers: dict[str, str] | None = None,
    json_error: bool = False,
):
    text = str(body)
    if json_error:
        raw = SimpleNamespace(json=lambda: (_ for _ in ()).throw(ValueError("no json")))
    else:
        raw = SimpleNamespace(json=lambda: body)
    return NormalizedResponse(
        status_code=status,
        headers=CaseInsensitiveDict(headers or {}),
        text=text,
        elapsed_ms=1.0,
        raw_response=raw,
    )


def test_async_poller_returns_status_response_result() -> None:
    calls: list[RequestSpec] = []
    responses = [
        _response(202, {"id": "job-1"}),
        _response(200, {"status": "completed", "result": {"items": [{"x": 1}]}}),
    ]

    def execute(spec: RequestSpec) -> NormalizedResponse:
        calls.append(spec)
        return responses.pop(0)

    result = AsyncPoller(execute=execute, sleep=lambda _: None).run(
        RequestSpec(url="https://api.test/run", method="POST"),
        {
            "job_id_path": "id",
            "status_url": "{base}/jobs/{job_id}",
            "poll_interval_s": 0,
        },
        base_url="https://api.test/run",
    )

    assert result.completed is True
    assert result.job_id == "job-1"
    assert result.data == {"items": [{"x": 1}]}
    assert calls[1].url == "https://api.test/jobs/job-1"
    assert calls[1].method == "GET"


def test_async_poller_can_return_incomplete_single_poll() -> None:
    responses = [
        _response(202, {"id": "job-1"}),
        _response(200, {"status": "running"}),
    ]

    def execute(_: RequestSpec) -> NormalizedResponse:
        return responses.pop(0)

    result = AsyncPoller(execute=execute, sleep=lambda _: None).run(
        RequestSpec(url="https://api.test/run", method="POST"),
        {
            "job_id_path": "id",
            "status_url": "{base}/jobs/{job_id}",
            "poll_interval_s": 0,
        },
        base_url="https://api.test/run",
        max_polls=1,
        allow_incomplete=True,
    )

    assert result.completed is False
    assert result.status == "running"
    assert result.data == {"status": "running"}


def test_async_poller_rejects_failed_status() -> None:
    responses = [
        _response(202, {"id": "job-1"}),
        _response(200, {"status": "failed"}),
    ]

    def execute(_: RequestSpec) -> NormalizedResponse:
        return responses.pop(0)

    with pytest.raises(ValueError, match="job failed"):
        AsyncPoller(execute=execute, sleep=lambda _: None).run(
            RequestSpec(url="https://api.test/run", method="POST"),
            {
                "job_id_path": "id",
                "status_url": "{base}/jobs/{job_id}",
                "poll_interval_s": 0,
            },
            base_url="https://api.test/run",
        )


def test_async_poller_submit_http_error_carries_response() -> None:
    response = _response(429, {"error": "slow down"})

    with pytest.raises(AsyncPollError) as exc_info:
        AsyncPoller(execute=lambda _spec: response, sleep=lambda _: None).run(
            RequestSpec(url="https://api.test/run", method="POST"),
            {
                "accepted_status": [202],
                "job_id_path": "id",
                "status_url": "{base}/jobs/{job_id}",
            },
            base_url="https://api.test/run",
        )

    assert exc_info.value.response is response
    assert exc_info.value.stage == "submit"
    assert "HTTP 429" in str(exc_info.value)


def test_async_poller_supports_header_job_id_and_result_url() -> None:
    calls: list[RequestSpec] = []
    responses = [
        _response(202, "", headers={"X-Job-Id": "job/1"}, json_error=True),
        _response(200, {"status": "completed", "result_url": "{base}/out/{job_id}"}),
        _response(200, {"items": [{"x": 2}]}),
    ]

    def execute(spec: RequestSpec) -> NormalizedResponse:
        calls.append(spec)
        return responses.pop(0)

    result = AsyncPoller(execute=execute, sleep=lambda _: None).run(
        RequestSpec(url="https://api.test/run", method="POST"),
        {
            "job_id_from": "header",
            "job_id_path": "X-Job-Id",
            "status_url": "{base}/jobs/{job_id}",
            "result_from": "result_url",
            "result_path": "result_url",
            "poll_interval_s": 0,
        },
        base_url="https://api.test/run",
    )

    assert result.completed is True
    assert result.data == {"items": [{"x": 2}]}
    assert calls[1].url == "https://api.test/jobs/job%2F1"
    assert calls[2].url == "https://api.test/out/job%2F1"


def test_validate_async_config_rejects_missing_status_url() -> None:
    with pytest.raises(ValueError, match="status_url"):
        validate_async_config({"enabled": True, "job_id_path": "id"})


def test_validate_async_config_requires_object() -> None:
    with pytest.raises(ValueError, match="oggetto"):
        validate_async_config(True)
