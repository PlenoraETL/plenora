"""Compatibility facade for legacy result-store imports.

New code should import the concrete results modules directly:
``blob_writer``, ``pipeline_result``, ``result_reader`` and ``temp_paths``.
"""

from __future__ import annotations

from app.core.results.blob_writer import (
    ResultSaveError,
    apply_results_retention_after_save as _apply_results_retention,
    cleanup_uploaded_result_blob as _cleanup_uploaded_result_blob,
    enforce_db_hard_cap as _enforce_db_hard_cap,
    save_result_blob,
    save_result_blob_from_bytes,
)
from app.core.results.pipeline_result import (
    PipelineResultSave,
    pipeline_result_save_legacy_payload as _pipeline_result_save_legacy_payload,
    save_pipeline_result,
    save_pipeline_result_outcome,
)
from app.core.results.result_reader import (
    get_result_as_bytes,
    get_result_as_tempfile,
    get_result_info,
)
from app.core.results.retention import DEFAULT_RESULTS_RETENTION
from app.core.results.storage_policy import (
    result_db_hard_cap as _result_db_hard_cap,
    result_s3_threshold as _result_s3_threshold,
    should_use_object_storage as _should_use_object_storage,
    storage_policy as _storage_policy,
)
from app.core.results.temp_paths import get_temp_output_path

__all__ = [
    "DEFAULT_RESULTS_RETENTION",
    "PipelineResultSave",
    "ResultSaveError",
    "_apply_results_retention",
    "_cleanup_uploaded_result_blob",
    "_enforce_db_hard_cap",
    "_pipeline_result_save_legacy_payload",
    "_result_db_hard_cap",
    "_result_s3_threshold",
    "_should_use_object_storage",
    "_storage_policy",
    "get_result_as_bytes",
    "get_result_as_tempfile",
    "get_result_info",
    "get_temp_output_path",
    "save_pipeline_result",
    "save_pipeline_result_outcome",
    "save_result_blob",
    "save_result_blob_from_bytes",
]
