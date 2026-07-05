"""Compatibility marker: nessuna API di materializzazione su disco.

I flussi che richiedono un path su disco devono usare:

  - `BytesIO` / `StringIO` per file-like API
  - `/vsimem` (libgdal ctypes adapter in `app.core.io.vsimem`)
    per binari OGR multi-file
  - object storage diretto (`save_bytes` su S3/MinIO) per persistence

Se torna a servire un disk-write boundary, NON resuscitare questo
modulo: aggiungere una capability esplicita su `app.core.storage`
con policy + scope checks invece di un context manager generico.
"""

from __future__ import annotations

__all__: list[str] = []
