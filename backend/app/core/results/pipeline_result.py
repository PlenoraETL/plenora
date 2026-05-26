"""Persist dataframe pipeline outputs as result artifacts.

Post-Phase5 refactor: bytes-only. Tutti i formati (csv/xlsx/json/parquet
e geospatial gpkg/shp/geojson) sono serializzati in memoria via
`save_dataframe_to_bytes`. Lo SHP multi-file e' zippato dentro il
boundary `io.geospatial.write_shapefile_zip_bytes` (vedi doc del
boundary), che e' l'unico punto autorizzato a touchare il disco.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TypeAlias

from app.core.results.blob_writer import save_result_blob_from_bytes


PipelineResultPayload: TypeAlias = dict[str, str]


@dataclass(frozen=True)
class PipelineResultSave:
    """Typed result for pipeline output persistence."""

    result_id: str
    output_filename: str
    output_columns: Optional[list[str]] = None


def pipeline_result_save_legacy_payload(
    outcome: PipelineResultSave,
) -> PipelineResultPayload:
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
) -> PipelineResultPayload:
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
    """Save a DataFrame as a result artifact through a single entry point.

    RAM-A1 (2026-05-26): usa il path streaming
    `save_dataframe_to_result_blob_streaming` che combina
    `dataframe_to_export_chunks` (CSV/JSON via queue+thread bridge) con
    `StreamingResultBlobWriter` (S3 multipart o DB BYTEA buffer). Per
    CSV/JSON peak ridotto da `df + bytes_intere` a `df + chunk_buffer`.
    Formati binari (parquet/xlsx/shp/gpkg/gdb) cadono su single-chunk
    by-design (footer-based o multi-file).
    """
    from app.core.results.streaming_blob_writer import (
        save_dataframe_to_result_blob_streaming,
    )

    fmt_lower = (output_format or "").lower()
    # SHP esce zippato via `io.geospatial.write_shapefile_zip_bytes`;
    # estensione del result blob diventa `.zip` (extracted by reader).
    if fmt_lower == "shp":
        filename = f"{filename_stem}.zip"
    else:
        filename = f"{filename_stem}.{fmt_lower}"

    result_id = save_dataframe_to_result_blob_streaming(
        project_id=project_id,
        df=df,
        output_format=fmt_lower,
        filename=filename,
        rows_count=rows_count,
        group_id=group_id,
        geom_column=geom_column,
        crs=crs,
    )
    return PipelineResultSave(result_id=result_id, output_filename=filename)


__all__ = [
    "PipelineResultSave",
    "pipeline_result_save_legacy_payload",
    "save_pipeline_result",
    "save_pipeline_result_outcome",
]
