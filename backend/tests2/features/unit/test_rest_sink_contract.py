from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pytest

from app.core.features.rest_sink import (
    RestFeatureSink,
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
        self.responses = responses or [RestSinkHttpResponse(status_code=201, body={"ok": True})]
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


def test_rest_sink_batches_records_and_maps_body_fields() -> None:
    client = FakeHttpClient(
        responses=[
            RestSinkHttpResponse(status_code=201, body={"batch": 1}),
            RestSinkHttpResponse(status_code=201, body={"batch": 2}),
        ]
    )
    config = RestSinkConfig(
        url="https://api.example.test/features",
        method=RestSinkMethod.POST,
        headers=(RestSinkHeader("X-Trace", "trace-1"),),
        body_mapping=RestSinkBodyMapping(
            fields=(
                RestSinkBodyField(source="id", target="external_id", required=True),
                RestSinkBodyField(source="name", target="label"),
            ),
            include_unmapped=False,
            batch_records_key="items",
        ),
        batch_mode=RestSinkBatchMode.BATCH,
        batch_size=2,
    )

    result = RestFeatureSink(config, client).write_records(
        [
            {"id": 1, "name": "Alpha", "ignored": "x"},
            {"id": 2, "name": "Beta", "ignored": "y"},
            {"id": 3, "name": "Gamma", "ignored": "z"},
        ]
    )

    assert result.success is True
    assert result.attempted_count == 3
    assert result.written_count == 3
    assert result.failed_count == 0
    assert len(client.requests) == 2
    assert client.requests[0].json == {
        "items": [
            {"external_id": 1, "label": "Alpha"},
            {"external_id": 2, "label": "Beta"},
        ]
    }
    assert client.requests[1].json == {
        "items": [{"external_id": 3, "label": "Gamma"}]
    }
    assert client.requests[0].method == "POST"
    assert client.requests[0].timeout == 30.0


def test_rest_sink_per_record_mode_emits_one_request_per_row() -> None:
    client = FakeHttpClient(
        responses=[
            RestSinkHttpResponse(status_code=204),
            RestSinkHttpResponse(status_code=204),
        ]
    )
    config = RestSinkConfig(
        url="https://api.example.test/rows",
        method=RestSinkMethod.PATCH,
        batch_mode=RestSinkBatchMode.PER_RECORD,
        success_statuses=(204,),
    )

    result = RestFeatureSink(config, client).write_records(
        [{"id": 1, "value": "a"}, {"id": 2, "value": "b"}]
    )

    assert result.success is True
    assert result.written_count == 2
    assert [request.json for request in client.requests] == [
        {"id": 1, "value": "a"},
        {"id": 2, "value": "b"},
    ]
    assert {request.method for request in client.requests} == {"PATCH"}


def test_rest_sink_failure_result_uses_redacted_request_snapshot() -> None:
    client = FakeHttpClient(
        responses=[RestSinkHttpResponse(status_code=500, text="upstream rejected secret-token")]
    )
    config = RestSinkConfig(
        url="https://api.example.test/features",
        headers=(
            RestSinkHeader("Authorization", "Bearer secret-token", secret=True),
            RestSinkHeader("X-Client", "plenora"),
        ),
    )

    result = RestFeatureSink(config, client).write_records([{"id": 1}])

    assert result.success is False
    assert result.written_count == 0
    assert result.failed_count == 1
    batch = result.batches[0]
    assert batch.request is not None
    assert batch.request.headers == {
        "Authorization": "***REDACTED***",
        "X-Client": "plenora",
    }
    assert client.requests[0].headers["Authorization"] == "Bearer secret-token"
    assert "secret-token" not in batch.error
    assert "Bearer secret-token" not in repr(result)
    assert "secret-token" not in repr(result)
    assert "secret-token" not in repr(batch.request)


def test_rest_sink_redacts_secret_header_when_client_raises() -> None:
    config = RestSinkConfig(
        url="https://api.example.test/features",
        headers=(RestSinkHeader("X-Api-Key", "secret-key", secret=True),),
    )
    client = FakeHttpClient(error=RuntimeError("connection refused: secret-key"))

    result = RestFeatureSink(config, client).write_records([{"id": 1}])

    assert result.success is False
    assert result.batches[0].status_code is None
    assert result.batches[0].error == "connection refused: ***REDACTED***"
    assert result.batches[0].request is not None
    assert result.batches[0].request.headers["X-Api-Key"] == "***REDACTED***"
    assert "secret-key" not in repr(result)


def test_rest_sink_validates_required_fields_and_safe_defaults() -> None:
    config = RestSinkConfig(
        url="https://api.example.test/features",
        body_mapping=RestSinkBodyMapping(
            fields=(RestSinkBodyField(source="id", target="id", required=True),)
        ),
    )

    result = RestFeatureSink(config, FakeHttpClient()).write_records([{"name": "missing"}])
    assert result.success is False
    assert result.failed_count == 1
    assert result.batches[0].error == "REST sink missing required field: id"

    with pytest.raises(ValueError, match="url must be absolute"):
        RestSinkConfig(url="/relative/path")

    with pytest.raises(ValueError, match="batch_size"):
        RestSinkConfig(url="https://api.example.test/features", batch_size=0)


def test_rest_sink_accepts_lazy_generator_input() -> None:
    """write_records accetta `Iterable[RestRecord]`, non solo Sequence.
    Verifica che un generator (no len, no random access) produca lo
    stesso payload e attempted_count corretto via accumulo per batch."""
    client = FakeHttpClient(
        responses=[
            RestSinkHttpResponse(status_code=201),
            RestSinkHttpResponse(status_code=201),
        ]
    )
    config = RestSinkConfig(
        url="https://api.example.test/features",
        batch_mode=RestSinkBatchMode.BATCH,
        batch_size=2,
    )

    def gen():
        for i in (1, 2, 3):
            yield {"id": i}

    result = RestFeatureSink(config, client).write_records(gen())

    assert result.success is True
    assert result.attempted_count == 3
    assert result.written_count == 3
    assert len(client.requests) == 2
    assert client.requests[0].json == {"records": [{"id": 1}, {"id": 2}]}
    assert client.requests[1].json == {"records": [{"id": 3}]}


def test_rest_sink_empty_iterable_produces_zero_attempted_no_request() -> None:
    """Iterable vuoto (anche generator): niente early-return,
    `_iter_batches` non yielda nulla, attempted=0 / batches=() /
    success=True."""
    client = FakeHttpClient()
    config = RestSinkConfig(
        url="https://api.example.test/features",
        batch_mode=RestSinkBatchMode.BATCH,
    )

    result = RestFeatureSink(config, client).write_records(iter(()))

    assert result.success is True
    assert result.attempted_count == 0
    assert result.written_count == 0
    assert result.failed_count == 0
    assert result.batches == ()
    assert client.requests == []


def test_rest_sink_per_record_mode_with_generator() -> None:
    """PER_RECORD su generator produce 1 request per record yielded,
    nessuna materializzazione upfront."""
    client = FakeHttpClient(
        responses=[
            RestSinkHttpResponse(status_code=204),
            RestSinkHttpResponse(status_code=204),
        ]
    )
    config = RestSinkConfig(
        url="https://api.example.test/rows",
        batch_mode=RestSinkBatchMode.PER_RECORD,
        success_statuses=(204,),
    )

    def gen():
        yield {"id": 1}
        yield {"id": 2}

    result = RestFeatureSink(config, client).write_records(gen())

    assert result.success is True
    assert result.attempted_count == 2
    assert result.written_count == 2
    assert [req.json for req in client.requests] == [{"id": 1}, {"id": 2}]


def test_rest_sink_generator_exception_propagates_to_caller() -> None:
    """Se il GENERATOR di record esplode mentre produce, write_records
    deve propagare l'eccezione al caller (no RestSinkWriteResult
    parziale). Il caller boundary lo mappera' a write_error."""
    client = FakeHttpClient(
        responses=[RestSinkHttpResponse(status_code=201)]
    )
    config = RestSinkConfig(
        url="https://api.example.test/features",
        batch_mode=RestSinkBatchMode.BATCH,
        batch_size=2,
    )

    class BoomRecords:
        def __iter__(self):
            yield {"id": 1}
            yield {"id": 2}
            raise RuntimeError("downstream pandas read exploded")

    with pytest.raises(RuntimeError, match="downstream pandas read exploded"):
        RestFeatureSink(config, client).write_records(BoomRecords())
