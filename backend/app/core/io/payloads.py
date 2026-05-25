"""Tipi canonici per il boundary I/O.

`BinaryPayload`: payload bytes/stream con metadata (filename,
content_type, size, sha256). Il tipo che i reader/writer scambiano
fra loro al posto di path fisici.

`StoredObjectRef`: riferimento opaco a un payload persistito su
DB/S3/MinIO/local-dev. Il codice business riceve il ref dopo
`storage.save(payload)` e lo passa a `storage.open(ref)` per
rileggere senza sapere quale backend lo ospita.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, BinaryIO, Mapping


@dataclass(frozen=True)
class BinaryPayload:
    """In-flight binary payload con metadata.

    `data` puo' essere `bytes` (small payload) o `BinaryIO`
    (stream per payload grandi). I reader/writer del boundary
    I/O accettano entrambi i casi e scelgono il path appropriato
    (parser RAM bytes vs streaming).

    `size` e `sha256` sono opzionali: se assenti, alcuni storage
    backend potrebbero calcolarli al save-time per metadata.
    """

    data: bytes | BinaryIO
    filename: str
    content_type: str
    size: int | None = None
    sha256: str | None = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class StoredObjectRef:
    """Riferimento opaco a un payload persistito.

    Mantiene insieme l'identificatore storage-specific
    (`storage_key` per object storage, `db_id` per DB BYTEA) e
    i metadata necessari per ricostruire il `BinaryPayload`
    quando il caller fa `storage.open(ref)`.

    `backend` discrimina il backend di storage ("db", "s3",
    "minio", "local_dev") e indica al boundary storage quale
    adapter usare per il read.
    """

    storage_key: str | None
    db_id: str | None
    filename: str
    content_type: str
    size: int
    sha256: str
    backend: str
    metadata: Mapping[str, Any] | None = None


__all__ = ["BinaryPayload", "StoredObjectRef"]
