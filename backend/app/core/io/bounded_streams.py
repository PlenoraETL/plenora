"""Primitive I/O bounded per evitare materializzazioni complete in RAM.

I payload grandi restano file-like seekable: fino a ``IO_SPOOL_MAX_MEMORY_BYTES``
lo spool usa memoria, oltre la soglia passa automaticamente a un file temporaneo
anonimo che viene eliminato alla chiusura. Nessun caller deve usare
``BytesIO.getvalue()`` per consegnare un blob all'object storage.
"""

from __future__ import annotations

from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Iterable, Optional


DEFAULT_CHUNK_BYTES = 1024 * 1024
DEFAULT_SPOOL_MEMORY_BYTES = 8 * 1024 * 1024


def spool_memory_limit() -> int:
    """Soglia RAM dello spool, letta lazy per supportare override runtime/test."""
    from app.config import settings

    value = int(
        settings.get("IO_SPOOL_MAX_MEMORY_BYTES", DEFAULT_SPOOL_MEMORY_BYTES)
    )
    # tempfile interpreta max_size=0 come "non fare rollover automatico",
    # cioe' l'opposto di un cap sicuro. Valori non positivi tornano al default.
    return value if value > 0 else DEFAULT_SPOOL_MEMORY_BYTES


def stream_size(stream: BinaryIO) -> int:
    """Ritorna la size di uno stream seekable preservandone la posizione."""
    position = stream.tell()
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(position)
    return int(size)


def read_prefix(stream: BinaryIO, size: int = 8192) -> bytes:
    """Legge solo l'header e ripristina la posizione originale."""
    position = stream.tell()
    stream.seek(0)
    data = stream.read(size)
    stream.seek(position)
    return data


def copy_to_spool(
    source: BinaryIO,
    *,
    max_bytes: Optional[int] = None,
    expected_size: Optional[int] = None,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
) -> BinaryIO:
    """Copia incrementale in uno spool seekable con cap hard opzionale.

    Il caller possiede lo stream ritornato e deve chiuderlo. In caso di errore
    lo spool viene chiuso qui, quindi non resta alcun file temporaneo.
    """
    spool = SpooledTemporaryFile(max_size=spool_memory_limit(), mode="w+b")
    total = 0
    try:
        while True:
            chunk = source.read(chunk_bytes)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise ValueError(
                    f"Stream espanso oltre il limite: {total} > {max_bytes} bytes"
                )
            spool.write(chunk)
        if expected_size is not None and total != expected_size:
            raise ValueError(
                f"Dimensione stream inattesa: letti {total}, attesi "
                f"{expected_size} bytes"
            )
        spool.seek(0)
        return spool
    except Exception:
        spool.close()
        raise


def copy_chunks_to_spool(
    chunks: Iterable[bytes],
    *,
    max_bytes: Optional[int] = None,
    expected_size: Optional[int] = None,
) -> BinaryIO:
    """Scrive un iterable di chunk nello spool senza concatenazione bytes."""
    spool = SpooledTemporaryFile(max_size=spool_memory_limit(), mode="w+b")
    total = 0
    iterator = iter(chunks)
    try:
        for chunk in iterator:
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise TypeError(
                    f"Chunk non bytes-like: {type(chunk).__name__}"
                )
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise ValueError(
                    f"Stream oltre il limite: {total} > {max_bytes} bytes"
                )
            spool.write(chunk)
        if expected_size is not None and total != expected_size:
            raise ValueError(
                f"Dimensione stream inattesa: letti {total}, attesi "
                f"{expected_size} bytes"
            )
        spool.seek(0)
        return spool
    except Exception:
        spool.close()
        raise
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()


__all__ = [
    "copy_to_spool",
    "copy_chunks_to_spool",
    "read_prefix",
    "spool_memory_limit",
    "stream_size",
]
