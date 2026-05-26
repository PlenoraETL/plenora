"""Tombstone post Punto 3 cleanup (2026-05-26).

Era il contract test del centralized path-only temp adapter
(`PATH_ONLY_ADAPTER_PREFIX`, `RESULT_STREAM_TEMP_PREFIX`,
`write_path_only_adapter_bytes`, `cleanup_path_only_adapter`,
`get_temp_output_path`). Tutti i symbol sono stati tombstonati in
`app/core/results/temp_paths.py` perche' senza caller runtime: il
result-writing del refactor passa da `StreamingResultBlobWriter`
(S3 multipart) + `save_result_blob_from_bytes` (DB BYTEA fallback).
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


def test_path_only_adapter_symbols_are_tombstoned():
    """Sentinel: nessuno dei path-only adapter symbol resta esposto."""
    import app.core.results.temp_paths as tp

    for legacy in (
        "PATH_ONLY_ADAPTER_PREFIX",
        "RESULT_STREAM_TEMP_PREFIX",
        "make_path_only_adapter_dir",
        "write_path_only_adapter_bytes",
        "cleanup_path_only_adapter",
        "get_temp_output_path",
    ):
        assert not hasattr(tp, legacy), (
            f"temp_paths.{legacy} ancora esposto: refactor RAM-only incompleto."
        )
