"""Persist dataframe pipeline outputs as result artifacts."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from app.core.results.blob_writer import save_result_blob_from_bytes
from app.core.results.temp_paths import make_pipeline_fs_temp_dir

settings = None


@dataclass(frozen=True)
class PipelineResultSave:
    """Typed result for pipeline output persistence."""

    result_id: str
    output_filename: str


def pipeline_result_save_legacy_payload(outcome: PipelineResultSave) -> Dict[str, str]:
    """Serialize the public save_pipeline_result compatibility payload."""
    return {
        "result_id": outcome.result_id,
        "output_filename": outcome.output_filename,
    }


def save_pipeline_result(
    project_id: str,
    df,
    output_format: str,
    filename_stem: str,
    rows_count: Optional[int] = None,
    group_id: Optional[str] = None,
    geom_column: Optional[str] = None,
    crs: str = "EPSG:4326",
) -> Dict[str, str]:
    """Compatibility wrapper returning legacy dict result metadata."""
    outcome = save_pipeline_result_outcome(
        project_id=project_id,
        df=df,
        output_format=output_format,
        filename_stem=filename_stem,
        rows_count=rows_count,
        group_id=group_id,
        geom_column=geom_column,
        crs=crs,
    )
    return pipeline_result_save_legacy_payload(outcome)


def save_pipeline_result_outcome(
    project_id: str,
    df,
    output_format: str,
    filename_stem: str,
    rows_count: Optional[int] = None,
    group_id: Optional[str] = None,
    geom_column: Optional[str] = None,
    crs: str = "EPSG:4326",
) -> PipelineResultSave:
    """Save a DataFrame as a result artifact through a single entry point."""
    from app.core.files.dataframe_io import (
        save_dataframe,
        save_dataframe_to_bytes,
    )

    fmt_lower = (output_format or "").lower()
    filename = f"{filename_stem}.{fmt_lower}"

    if fmt_lower in ("gpkg", "shp"):
        active_settings = settings
        if active_settings is None:
            from app.config import settings as active_settings

        tmp_dir = make_pipeline_fs_temp_dir(Path(active_settings.DATA_DIR))
        try:
            tmp_path = tmp_dir / filename
            saved_path = save_dataframe(df, tmp_path, output_format, geom_column, crs)
            actual_fmt = saved_path.suffix.lstrip(".") if fmt_lower == "shp" else fmt_lower
            result_id = save_result_blob_from_bytes(
                project_id=project_id,
                data=saved_path.read_bytes(),
                filename=saved_path.name,
                fmt=actual_fmt,
                rows_count=rows_count,
                group_id=group_id,
            )
            output_filename = saved_path.name
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return PipelineResultSave(
            result_id=result_id,
            output_filename=output_filename,
        )

    data = save_dataframe_to_bytes(df, fmt_lower, geom_column=geom_column, crs=crs)
    result_id = save_result_blob_from_bytes(
        project_id=project_id,
        data=data,
        filename=filename,
        fmt=fmt_lower,
        rows_count=rows_count,
        group_id=group_id,
    )
    return PipelineResultSave(result_id=result_id, output_filename=filename)


__all__ = [
    "PipelineResultSave",
    "pipeline_result_save_legacy_payload",
    "save_pipeline_result",
    "save_pipeline_result_outcome",
]
