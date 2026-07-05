"""Compatibility marker: nessuna API path-only per i result blob.

Questo modulo non esporta piu' symbol path-only:
  - `PATH_ONLY_ADAPTER_PREFIX`, `RESULT_STREAM_TEMP_PREFIX`
  - `make_path_only_adapter_dir`, `write_path_only_adapter_bytes`,
    `cleanup_path_only_adapter`
  - `get_temp_output_path`

Il result-writing passa da `StreamingResultBlobWriter` (S3 multipart via
boto3 client diretto) e `save_result_blob_from_bytes` (DB BYTEA fallback).
Le adapter path-only restano non disponibili per evitare regressioni verso
scritture temporanee su filesystem.
"""

from __future__ import annotations

__all__: list[str] = []
