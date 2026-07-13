"""
Bounded stream reader — single shared helper.

"""

from __future__ import annotations

from typing import Iterable, Iterator

from app.core.resources.exceptions import MemoryLimitExceededError


__all__ = ["iter_stream_bounded", "read_stream_bounded"]


# Bytes-like types accepted as chunks. `memoryview` and `bytearray`
# are accepted because requests / object-storage backends can
# yield either; append normalizes with `bytes(chunk)`.
_BYTES_LIKE = (bytes, bytearray, memoryview)


def iter_stream_bounded(
    body: Iterable[bytes],
    *,
    max_bytes: int,
    label: str,
    stage: str = "iter_stream_bounded",
) -> Iterator[bytes]:
    """Valida e inoltra chunk senza concatenarli in memoria.

    Il controllo e' pre-yield: il chunk che supera il cap non viene mai
    consegnato al consumer. Se l'iteratore espone ``close()``, viene chiuso
    anche su errore o abbandono anticipato.
    """
    if max_bytes < 0:
        raise ValueError(
            f"iter_stream_bounded: max_bytes must be >= 0, got {max_bytes!r}"
        )
    iterator = iter(body)
    running = 0
    try:
        for idx, chunk in enumerate(iterator):
            if not isinstance(chunk, _BYTES_LIKE):
                raise TypeError(
                    f"iter_stream_bounded({label!r}): chunk #{idx} is not "
                    f"bytes-like (got {type(chunk).__name__!r})"
                )
            next_size = running + len(chunk)
            if next_size > max_bytes:
                raise MemoryLimitExceededError(
                    observed_bytes=next_size,
                    limit_bytes=max_bytes,
                    path_basename=label,
                    stage=stage,
                )
            running = next_size
            yield chunk if isinstance(chunk, bytes) else bytes(chunk)
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()


def read_stream_bounded(
    body: Iterable[bytes],
    *,
    max_bytes: int,
    label: str,
    stage: str = "read_stream_bounded",
) -> bytes:
    """Read ``body`` into a single ``bytes`` value, bounded by
    ``max_bytes``.

    Args:
        body: iterable of bytes-like chunks (`bytes`, `bytearray`,
            or `memoryview`). Iterated exactly once. The
            iterator is NOT fully consumed on overrun: we raise
            as soon as the cap would be exceeded by appending the
            current chunk.
        max_bytes: maximum total bytes to accept (inclusive). The
            value MUST be ``>= 0``; negative values raise
            ``ValueError`` at call time (programming error).
        label: operator-visible identifier propagated to
            ``MemoryLimitExceededError.path_basename`` on overrun.
            Typically a short result_id / storage_key suffix /
            file basename. NEVER an absolute path (avoid leaking
            filesystem layout to the client via the boundary
            handler).
        stage: operator-visible stage propagated to
            ``MemoryLimitExceededError.stage``. Defaults to the
            helper name; callers can provide a narrower resource
            stage without reimplementing the bounded read loop.

    Returns:
        Concatenated bytes from every chunk consumed up to and
        including the limit. Empty input → ``b""``.

    Raises:
        ValueError: if ``max_bytes < 0``.
        TypeError: if any chunk is not bytes-like (with the
            chunk's index and observed type for diagnosis).
        MemoryLimitExceededError: cumulative bytes would exceed
            ``max_bytes``. ``observed_bytes`` is set to the
            running total at the point of detection
            (``running + len(over_limit_chunk)``), so the
            operator sees how much was being assembled when the
            cap fired. ``stage`` defaults to
            ``"read_stream_bounded"`` and can be narrowed by
            callers without adding new error codes.
    """
    if max_bytes < 0:
        raise ValueError(
            f"read_stream_bounded: max_bytes must be >= 0, got "
            f"{max_bytes!r}"
        )

    buffer = bytearray()
    running = 0

    for idx, chunk in enumerate(body):
        if not isinstance(chunk, _BYTES_LIKE):
            raise TypeError(
                f"read_stream_bounded({label!r}): chunk #{idx} is "
                f"not bytes-like (got {type(chunk).__name__!r}). "
                "Expected bytes / bytearray / memoryview."
            )
        chunk_len = len(chunk)
        # Pre-append size check: the over-limit chunk does NOT
        # enlarge the buffer. Memory bound: peak is
        # `running + chunk_len` for the brief moment between
        # this check and the raise
        if running + chunk_len > max_bytes:
            raise MemoryLimitExceededError(
                observed_bytes=running + chunk_len,
                limit_bytes=max_bytes,
                path_basename=label,
                stage=stage,
            )
        # Normalize bytearray / memoryview to bytes via the
        # buffer's `extend` (zero-copy for bytes-like objects
        buffer.extend(chunk)
        running += chunk_len

    return bytes(buffer)
