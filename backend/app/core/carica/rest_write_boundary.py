"""CARICA-owned REST write boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.connections.rest_sink import (
    RestFeatureSink,
    RestSinkBatchResult,
    RestSinkConfig,
    RestSinkHttpClient,
    RestSinkWriteResult,
)
from app.core.dataframes.tabular import iter_dataframe_rest_records
from app.shared.contracts.payloads import ObjectMap
from app.utils.logger import get_logger


logger = get_logger(__name__)


@dataclass(frozen=True)
class RestBatchSummary:
    success: bool
    status_code: int | None
    sent_count: int
    error: str

    def to_task_result(self) -> ObjectMap:
        return {
            "success": self.success,
            "status_code": self.status_code,
            "sent_count": self.sent_count,
            "error": self.error,
        }


@dataclass(frozen=True)
class CaricaRestWriteOutcome:
    """Domain outcome converted to the task/API result shape."""

    status: str
    success: bool
    rows_written: int
    rows_inserted: int
    rows_updated: int
    rows_failed: int
    total_rows: int
    message: str
    batches: tuple[RestBatchSummary, ...] = ()
    error: str = ""
    error_message: str = ""

    def to_task_result(self) -> ObjectMap:
        result: ObjectMap = {
            "status": self.status,
            "success": self.success,
            "rows_written": self.rows_written,
            "rows_inserted": self.rows_inserted,
            "rows_updated": self.rows_updated,
            "rows_failed": self.rows_failed,
            "total_rows": self.total_rows,
            "message": self.message,
            "batches": [batch.to_task_result() for batch in self.batches],
        }
        if not self.success:
            result["error"] = self.error or "carica_rest_write_failed"
            result["error_message"] = self.error_message or self.message
        return result


def execute_carica_rest_write(
    *,
    df: Any,
    config: RestSinkConfig,
    http_client: RestSinkHttpClient,
) -> CaricaRestWriteOutcome:
    """Write a CARICA DataFrame through the typed REST sink."""
    total_rows = len(df)
    try:
        write_result = RestFeatureSink(config, http_client).write_records(
            iter_dataframe_rest_records(df)
        )
    except Exception as exc:
        logger.error("[GESTITO] Errore scrittura REST CARICA: %s", exc, exc_info=True)
        message = _redact_secret_values(str(exc), config)
        return CaricaRestWriteOutcome(
            status="write_error",
            success=False,
            rows_written=0,
            rows_inserted=0,
            rows_updated=0,
            rows_failed=total_rows,
            total_rows=total_rows,
            message=message,
            error="carica_rest_write_failed",
            error_message=message,
        )

    return _to_outcome(write_result, total_rows)


def _to_outcome(result: RestSinkWriteResult, total_rows: int) -> CaricaRestWriteOutcome:
    # Il sink interrompe intenzionalmente i batch successivi su 401/403.
    # Quelle righe non sono "attempted", ma per il contratto CARICA restano
    # comunque non scritte e devono comparire nel conteggio dei fallimenti.
    rows_failed = max(total_rows - result.written_count, 0)
    if result.success:
        return CaricaRestWriteOutcome(
            status="completed",
            success=True,
            rows_written=result.written_count,
            rows_inserted=result.written_count,
            rows_updated=0,
            rows_failed=rows_failed,
            total_rows=total_rows,
            message=f"Scrittura REST completata: {result.written_count} righe",
            batches=_batch_summaries(result.batches),
        )

    status = "partial" if result.written_count > 0 else "write_error"
    message = _failure_message(result)
    return CaricaRestWriteOutcome(
        status=status,
        success=False,
        rows_written=result.written_count,
        rows_inserted=result.written_count,
        rows_updated=0,
        rows_failed=rows_failed,
        total_rows=total_rows,
        message=message,
        batches=_batch_summaries(result.batches),
        error="carica_rest_write_failed",
        error_message=message,
    )

def _batch_summaries(
    batches: tuple[RestSinkBatchResult, ...],
) -> tuple[RestBatchSummary, ...]:
    return tuple(
        RestBatchSummary(
            success=batch.success,
            status_code=batch.status_code,
            sent_count=batch.sent_count,
            error=batch.error,
        )
        for batch in batches
    )


def _failure_message(result: RestSinkWriteResult) -> str:
    errors = [batch.error for batch in result.batches if batch.error]
    if errors:
        return errors[0]
    return (
        "Scrittura REST completata parzialmente"
        if result.written_count > 0
        else "Scrittura REST fallita"
    )


def _redact_secret_values(value: str, config: RestSinkConfig) -> str:
    redacted = value
    for header in config.headers:
        if header.secret and header.value:
            redacted = redacted.replace(header.value, "***REDACTED***")
            for token in header.value.split():
                if len(token) > 3:
                    redacted = redacted.replace(token, "***REDACTED***")
    return redacted
