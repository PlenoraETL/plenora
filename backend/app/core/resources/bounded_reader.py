"""Bounded stream reader — single shared helper.

BE-GO-READY-RESOURCE-ENGINE-009-BOUNDED-STREAM-READER.

Problem:
Several engine paths consume an `Iterable[bytes]` body
(object-storage `get_object_stream`, requests' `iter_content`,
psycopg2 large-object streams, etc.) and need to materialize it
into bytes. The naive shape

::

    data = b"".join(body)

works for in-memory development but is unbounded: a 50 GB body
silently fills RAM and OOM-kills the worker. Equivalent failure
mode for ``list(body)`` followed by ``b"".join(...)``.

Anti-god-engine + scope:
This module is the single canonical bounded reader for the
resource engine. It is **stdlib-only** (no FastAPI, no DB, no
config, no boto3 / requests), so any caller — loader, worker
helper, eseguibili download, IMDF bundle stream — can import it
without dragging a heavy dependency graph.

The helper does NOT:
- decide what `max_bytes` means for a given context (callers
  pass it explicitly);
- raise HTTP exceptions (mapping HTTP 413 lives at the boundary
  in `app/main.py`);
- log structured timing (the loader / caller owns that surface);
- retry, parse, or transform the bytes.

It does ONE thing: read at most `max_bytes` from an iterable of
bytes-like chunks, fail-loud when exceeded, and return the joined
bytes on success.

Memory bound:
Peak memory while reading is bounded to:

  ``running_size + len(current_chunk) <= max_bytes + len(largest_chunk)``

We accumulate into a single `bytearray` (one O(n) allocation
total over the body's lifetime, vs the multi-allocation cost of
`list(body) + b"".join(...)`). The check fires **before** the
chunk is appended, so the over-limit chunk does not enlarge the
buffer beyond `max_bytes + len(over_limit_chunk)` for the brief
moment between the size check and the raise.

Failure semantics:
- ``max_bytes < 0`` → `ValueError` at call time (programming
  error, not a runtime bound).
- Non-bytes-like chunk (`int`, `str`, `None`, etc.) → `TypeError`
  with the chunk's offset and observed type. Fails fast and
  clearly; partial buffer is dropped.
- Cumulative bytes > `max_bytes` → `MemoryLimitExceededError`
  with `observed_bytes` set to the running total at the point
  of detection (`running_size + len(chunk_that_triggered)`),
  `limit_bytes=max_bytes`, `path_basename=label`,
  `stage="read_stream_bounded"`. The iterator is NOT fully
  consumed: we raise as soon as the cap would be exceeded.

The `MemoryLimitExceededError` re-uses the existing typed domain
exception (BE-GO-READY-MEMORY-OOM-001). HTTP-413 mapping is
already wired at the boundary; no new error code is needed.

Type contract:
- `body`: `Iterable[bytes]`. May be a generator, a list, a
  requests `iter_content`, an object-storage stream body. The
  helper iterates exactly once and stops on first overrun.
- `max_bytes`: int >= 0. The cap (inclusive).
- `label`: str. Passed to `MemoryLimitExceededError.path_basename`
  for operator visibility — typically a `result_id` /
  `storage_key` short form / file_source basename.

Returns
-------
`bytes`: the concatenation of every chunk read up to (and
including) the limit. Empty input yields `b""`.

Future migration:
Worker 3/4 own the concrete migrations to existing call sites:
- `result_blob.py::_download_object_storage` (currently
  `list(stream["body"])` then `b"".join(...)` — unbounded).
- `eseguibili` download paths.
- `imdf` bundle materialization.
This stream introduces only the helper + tests; concrete
migrations are explicitly out of scope.
"""

from __future__ import annotations

from typing import Iterable

from app.shared.exceptions import MemoryLimitExceededError


__all__ = ["read_stream_bounded"]


# Bytes-like types accepted as chunks. `memoryview` and `bytearray`
# are accepted because requests / object-storage backends can
# return either; we normalize via `bytes(chunk)` on append.
_BYTES_LIKE = (bytes, bytearray, memoryview)


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
        # this check and the raise.
        if running + chunk_len > max_bytes:
            raise MemoryLimitExceededError(
                observed_bytes=running + chunk_len,
                limit_bytes=max_bytes,
                path_basename=label,
                stage=stage,
            )
        # Normalize bytearray / memoryview to bytes via the
        # buffer's `extend` (zero-copy for bytes-like objects).
        buffer.extend(chunk)
        running += chunk_len

    return bytes(buffer)
