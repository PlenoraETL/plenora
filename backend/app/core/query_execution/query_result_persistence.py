"""Persistence helpers for QueryExecutor results.

QueryExecutor owns query execution and orchestration. This module owns the
result-store calls so executor code does not import low-level persistence
functions directly.

Il path streamable evita il tempfile intermedio.
`persist_streaming_query_to_storage` orchestra
`StreamingResultBlobWriter` (S3 multipart o DB BYTEA) +
`execute_streaming_to_writer` (cursor -> file-like).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from app.core.dataframes.ports import FrameLike
from app.core.results.pipeline_result import save_pipeline_result_outcome
from app.core.results.streaming_blob_writer import StreamingResultBlobWriter
from app.core.query_execution.query_streaming import (
    StreamingConnection,
    StreamingEngineFactory,
    execute_streaming_to_writer,
)
from app.db.sql_types import QueryParams


@dataclass(frozen=True)
class PersistedQueryResult:
    result_id: Optional[str]
    output_path: Path
    storage_key: Optional[str] = None


@dataclass(frozen=True)
class StreamedQueryResult:
    result_id: Optional[str]
    rows_count: int
    columns: List[str]
    output_path: Optional[Path] = None
    storage_key: Optional[str] = None


class QueryResultPersistence:
    """Persistence collaborator for query result outputs."""

    def persist_streaming_query_to_storage(
        self,
        *,
        project_id: str,
        engine_factory: StreamingEngineFactory,
        connection: StreamingConnection,
        sql: str,
        params: Optional[QueryParams],
        output_format: str,
        output_filename: str,
        group_id: Optional[str] = None,
    ) -> StreamedQueryResult:
        """Streaming end-to-end: query cursor → result blob storage.

        Crea uno `StreamingResultBlobWriter` (S3 multipart o DB BYTEA
        secondo policy) e ci passa il cursor query via
        `execute_streaming_to_writer`. Niente tempfile intermedio.

        Returns:
            `StreamedQueryResult` con result_id, rows_count, columns.

        Raises:
            Propaga errori di query, streaming write, storage upload.
            Su exception: aborta multipart (S3) + scarta buffer (DB).
        """
        writer = StreamingResultBlobWriter(
            project_id=project_id,
            filename=output_filename,
            fmt=output_format,
            group_id=group_id,
        )
        try:
            rows_count, columns = execute_streaming_to_writer(
                engine_factory=engine_factory,
                connection=connection,
                sql=sql,
                params=params,
                output_writer=writer,
                output_format=output_format,
            )
            writer.update_rows_count(rows_count)
            result_id = writer.finalize()
        except Exception:
            writer.abort()
            raise
        return StreamedQueryResult(
            result_id=result_id,
            rows_count=rows_count,
            columns=columns,
        )

    def persist_dataframe_result(
        self,
        *,
        project_id: str,
        df: FrameLike,
        output_format: str,
        filename_stem: str,
        rows_count: int,
        group_id: Optional[str] = None,
        geom_column: Optional[str] = None,
    ) -> PersistedQueryResult:
        saved = save_pipeline_result_outcome(
            project_id=project_id,
            df=df,
            output_format=output_format,
            filename_stem=filename_stem,
            rows_count=rows_count,
            group_id=group_id,
            geom_column=geom_column,
        )
        return PersistedQueryResult(
            result_id=saved.result_id,
            output_path=Path(saved.output_filename),
        )
