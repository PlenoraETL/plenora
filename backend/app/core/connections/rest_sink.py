"""Typed REST write sink foundation for row and feature payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Iterator, Mapping, Protocol, Sequence


RestScalar = str | int | float | bool | None
RestRecord = Mapping[str, RestScalar]
RestJsonValue = RestScalar | dict[str, "RestJsonValue"] | list["RestJsonValue"]
RestJsonObject = dict[str, RestJsonValue]
RestHeaders = dict[str, str]


class RestSinkMethod(str, Enum):
    """HTTP methods supported by the write sink."""

    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"


class RestSinkBatchMode(str, Enum):
    """How records are serialized into outbound HTTP requests."""

    PER_RECORD = "per_record"
    BATCH = "batch"


@dataclass(frozen=True)
class RestSinkHeader:
    """One outbound REST header.

    Secret headers are sent to the injected client but are redacted from all
    result objects returned by this module.
    """

    name: str
    value: str
    secret: bool = False


@dataclass(frozen=True)
class RestSinkBodyField:
    """Map one source record field to one JSON body field."""

    source: str
    target: str
    required: bool = False
    default: RestScalar = None


@dataclass(frozen=True)
class RestSinkBodyMapping:
    """Body serialization rules for rows/features."""

    fields: tuple[RestSinkBodyField, ...] = ()
    include_unmapped: bool = True
    batch_records_key: str = "records"


@dataclass(frozen=True)
class RestSinkConfig:
    """Typed configuration for REST writes."""

    url: str
    method: RestSinkMethod = RestSinkMethod.POST
    headers: tuple[RestSinkHeader, ...] = ()
    body_mapping: RestSinkBodyMapping = field(default_factory=RestSinkBodyMapping)
    batch_mode: RestSinkBatchMode = RestSinkBatchMode.BATCH
    batch_size: int = 100
    timeout_seconds: float = 30.0
    success_statuses: tuple[int, ...] = (200, 201, 202, 204)

    def __post_init__(self) -> None:
        if not self.url.startswith(("http://", "https://")):
            raise ValueError("REST sink url must be absolute http/https")
        if self.batch_size < 1:
            raise ValueError("REST sink batch_size must be >= 1")
        if self.timeout_seconds <= 0:
            raise ValueError("REST sink timeout_seconds must be > 0")
        if not self.body_mapping.batch_records_key:
            raise ValueError("REST sink batch_records_key cannot be empty")


@dataclass(frozen=True)
class RestSinkHttpResponse:
    """Minimal response contract expected from injected HTTP clients."""

    status_code: int
    body: RestJsonValue = None
    text: str = ""


class RestSinkHttpClient(Protocol):
    """Injected HTTP client; implementations may wrap requests/httpx/etc."""

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json: RestJsonValue,
        timeout: float,
    ) -> RestSinkHttpResponse:
        ...


@dataclass(frozen=True)
class RestSinkRequestSnapshot:
    """Redacted request metadata safe for errors/log-shaped results."""

    method: str
    url: str
    headers: dict[str, str]
    record_count: int


@dataclass(frozen=True)
class RestSinkBatchResult:
    """Outcome for one emitted HTTP request."""

    success: bool
    status_code: int | None
    sent_count: int
    response_body: RestJsonValue = None
    error: str = ""
    request: RestSinkRequestSnapshot | None = None


@dataclass(frozen=True)
class RestSinkWriteResult:
    """Aggregate REST write outcome."""

    success: bool
    attempted_count: int
    written_count: int
    failed_count: int
    batches: tuple[RestSinkBatchResult, ...]


class RestFeatureSink:
    """Core REST write sink with no environment reads and no owned network stack."""

    def __init__(self, config: RestSinkConfig, http_client: RestSinkHttpClient) -> None:
        self._config = config
        self._http_client = http_client

    def write_records(self, records: Iterable[RestRecord]) -> RestSinkWriteResult:
        """Write records through the injected HTTP client.

        Accepts any `Iterable[RestRecord]` (incluso generator lazy
        come `iter_dataframe_rest_records`). Niente early-return su
        empty: se l'iterable non yielda nulla, `_iter_batches` non
        yielda batch, il risultato finale e' attempted=0, success=
        True, batches=() — semantica preservata, niente fast path.
        `attempted_count` viene accumulato batch-per-batch invece di
        `len(records)` upfront (incompatibile con generator).
        Eccezioni emesse durante l'iterazione del generator
        propagano al caller (rest_write_boundary le mappa a
        write_error); HTTP fail invece sono catturati da
        `_send_batch` e producono batch fallito senza fermare gli
        altri batch.
        """
        batch_results: list[RestSinkBatchResult] = []
        attempted_count = 0
        for batch in self._iter_batches(records):
            attempted_count += len(batch)
            batch_results.append(self._send_batch(batch))

        written_count = sum(result.sent_count for result in batch_results if result.success)
        failed_count = attempted_count - written_count
        return RestSinkWriteResult(
            success=failed_count == 0,
            attempted_count=attempted_count,
            written_count=written_count,
            failed_count=failed_count,
            batches=tuple(batch_results),
        )

    def build_payload(self, records: Sequence[RestRecord]) -> RestJsonValue:
        """Build the JSON body for a request without sending it."""
        mapped_records = [self._map_record(record) for record in records]
        if self._config.batch_mode == RestSinkBatchMode.PER_RECORD:
            if len(mapped_records) != 1:
                raise ValueError("PER_RECORD payload requires exactly one record")
            return mapped_records[0]
        return {self._config.body_mapping.batch_records_key: mapped_records}

    def _send_batch(self, records: Sequence[RestRecord]) -> RestSinkBatchResult:
        request = RestSinkRequestSnapshot(
            method=self._config.method.value,
            url=self._config.url,
            headers=self._redacted_headers(),
            record_count=len(records),
        )
        try:
            response = self._http_client.request(
                method=self._config.method.value,
                url=self._config.url,
                headers=self._headers(),
                json=self.build_payload(records),
                timeout=self._config.timeout_seconds,
            )
        except Exception as exc:
            return RestSinkBatchResult(
                success=False,
                status_code=None,
                sent_count=0,
                error=self._redact_secret_values(str(exc)),
                request=request,
            )

        success = response.status_code in self._config.success_statuses
        return RestSinkBatchResult(
            success=success,
            status_code=response.status_code,
            sent_count=len(records) if success else 0,
            response_body=response.body,
            error="" if success else self._failure_message(response),
            request=request,
        )

    def _iter_batches(self, records: Iterable[RestRecord]) -> Iterator[list[RestRecord]]:
        """Yield batches lazily — ogni batch e' una list materializzata.

        Lazy sui record (no `len(records)` upfront), ma ogni batch
        yielded e' una `list[RestRecord]` concreta perche'
        `_send_batch`, `build_payload` e la validazione PER_RECORD
        (`len(mapped_records) != 1`) hanno bisogno di una sequenza
        random-access.
        """
        if self._config.batch_mode == RestSinkBatchMode.PER_RECORD:
            for record in records:
                yield [record]
            return

        size = self._config.batch_size
        batch: list[RestRecord] = []
        for record in records:
            batch.append(record)
            if len(batch) >= size:
                yield batch
                batch = []
        if batch:
            yield batch

    def _map_record(self, record: RestRecord) -> RestJsonObject:
        mapping = self._config.body_mapping
        payload: RestJsonObject = dict(record) if mapping.include_unmapped else {}

        for body_field in mapping.fields:
            value = record.get(body_field.source, body_field.default)
            if body_field.required and value is None:
                raise ValueError(f"REST sink missing required field: {body_field.source}")
            if value is not None or body_field.target in payload or body_field.default is not None:
                payload[body_field.target] = value
            if body_field.source != body_field.target:
                payload.pop(body_field.source, None)

        return payload

    def _headers(self) -> RestHeaders:
        return {header.name: header.value for header in self._config.headers}

    def _redacted_headers(self) -> RestHeaders:
        return {
            header.name: "***REDACTED***" if header.secret else header.value
            for header in self._config.headers
        }

    def _failure_message(self, response: RestSinkHttpResponse) -> str:
        if response.text:
            return self._redact_secret_values(
                f"REST sink request failed with status {response.status_code}: {response.text}"
            )
        return f"REST sink request failed with status {response.status_code}"

    def _redact_secret_values(self, value: str) -> str:
        redacted = value
        for header in self._config.headers:
            if header.secret and header.value:
                redacted = redacted.replace(header.value, "***REDACTED***")
                for token in header.value.split():
                    if len(token) > 3:
                        redacted = redacted.replace(token, "***REDACTED***")
        return redacted


__all__ = [
    "RestFeatureSink",
    "RestJsonValue",
    "RestRecord",
    "RestScalar",
    "RestSinkBatchMode",
    "RestSinkBatchResult",
    "RestSinkBodyField",
    "RestSinkBodyMapping",
    "RestSinkConfig",
    "RestSinkHeader",
    "RestSinkHttpClient",
    "RestSinkHttpResponse",
    "RestSinkMethod",
    "RestSinkRequestSnapshot",
    "RestSinkWriteResult",
]
