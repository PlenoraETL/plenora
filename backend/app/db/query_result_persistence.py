"""Persistence helpers for QueryExecutor results.

QueryExecutor owns query execution and orchestration. This module owns the
result-store calls so executor code does not import low-level persistence
functions directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from app.core.results.result_store import (
    get_temp_output_path,
    save_pipeline_result_outcome,
    save_result_blob,
)


@dataclass(frozen=True)
class PersistedQueryResult:
    result_id: str
    output_path: Path


class QueryResultPersistence:
    """Persistence collaborator for query result outputs."""

    def streaming_output_path(self, filename_stem: str, output_format: str) -> Path:
        return get_temp_output_path(filename_stem).with_suffix(f".{output_format}")

    def persist_streamed_file(
        self,
        *,
        project_id: str,
        output_path: Path,
        output_format: str,
        rows_count: int,
        group_id: Optional[str] = None,
    ) -> PersistedQueryResult:
        result_id = save_result_blob(
            project_id,
            Path(output_path),
            output_format,
            rows_count,
            group_id=group_id,
        )
        return PersistedQueryResult(
            result_id=result_id,
            output_path=Path(output_path),
        )

    def persist_dataframe_result(
        self,
        *,
        project_id: str,
        df: pd.DataFrame,
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
