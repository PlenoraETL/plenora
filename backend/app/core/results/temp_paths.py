"""Tombstone post Punto 3 cleanup (2026-05-25).

Era il modulo per path-only adapter (file-based libraries che
non supportano BytesIO / /vsimem). Symbol legacy:
  - `PATH_ONLY_ADAPTER_PREFIX`, `RESULT_STREAM_TEMP_PREFIX`
  - `make_path_only_adapter_dir`, `write_path_only_adapter_bytes`,
    `cleanup_path_only_adapter`
  - `get_temp_output_path`

Zero caller runtime: tutto il result-writing del refactor passa
da `StreamingResultBlobWriter` (S3 multipart via boto3 client
diretto) + `save_result_blob_from_bytes` (DB BYTEA fallback).
Le adapter path-only sono state rese inutili dalla migrazione
RAM-only di pyogrio (0.12+: bytes nativi) e di pyarrow
(ParquetWriter su BytesIO).

Riferimenti:
  - refactor_io_storage_ram_only.md §"Punto 3 — cleanup dead boundary"
  - tests2/io_boundary/test_no_direct_filesystem_contract.py
    (snapshot ratchet: questo file non e' piu' in `_BOUNDARY_FROZEN_SNAPSHOT`)
"""

from __future__ import annotations

__all__: list[str] = []
