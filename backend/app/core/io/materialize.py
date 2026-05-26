"""Tombstone post Punto 3 cleanup (2026-05-25).

Era il boundary autorizzato a materializzare bytes su filesystem
(`temporary_path_from_bytes`, `DiskWriteDeniedError`). Zero caller
runtime sopravvissuti al refactor RAM-only: tutti i flussi che
richiedevano un path su disco sono stati migrati a:

  - `BytesIO` / `StringIO` per file-like API
  - `/vsimem` (libgdal ctypes adapter in `app.core.io.vsimem`)
    per binari OGR multi-file
  - object storage diretto (`save_bytes` su S3/MinIO) per persistence

Riferimenti:
  - refactor_io_storage_ram_only.md §"Punto 3 — cleanup dead boundary"
  - tests2/io_boundary/test_no_direct_filesystem_contract.py
    (snapshot ratchet: questo file non e' piu' in `_BOUNDARY_FROZEN_SNAPSHOT`)

Se torna a servire un disk-write boundary, NON resuscitare questo
modulo: aggiungere una capability esplicita su `app.core.storage`
con policy + scope checks invece di un context manager generico.
"""

from __future__ import annotations

__all__: list[str] = []
