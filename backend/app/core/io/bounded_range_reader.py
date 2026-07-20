"""Seekable, memory-bounded view over a remotely ranged object."""

from __future__ import annotations

import io
from collections.abc import Callable

from app.core.resources.exceptions import MemoryLimitExceededError


class BoundedRangeReader(io.RawIOBase):
    """Expose remote byte ranges as a seekable binary file.

    The budget counts the union of fetched ranges, so repeated or overlapping
    reads do not create false over-limit failures. The class keeps no payload
    cache: decoded consumers own only the bytes returned by their current read.
    """

    def __init__(
        self,
        *,
        size: int,
        read_range: Callable[[int, int], bytes],
        max_bytes: int,
        label: str,
        stage: str,
    ) -> None:
        super().__init__()
        if size < 0:
            raise ValueError(f"size must be >= 0, got {size!r}")
        if max_bytes < 0:
            raise ValueError(f"max_bytes must be >= 0, got {max_bytes!r}")
        self._size = int(size)
        self._read_range = read_range
        self._max_bytes = int(max_bytes)
        self._label = label
        self._stage = stage
        self._position = 0
        self._fetched: list[tuple[int, int]] = []

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        self._checkClosed()
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        self._checkClosed()
        if whence == io.SEEK_SET:
            position = offset
        elif whence == io.SEEK_CUR:
            position = self._position + offset
        elif whence == io.SEEK_END:
            position = self._size + offset
        else:
            raise ValueError(f"invalid whence: {whence!r}")
        if position < 0:
            raise ValueError("negative seek position")
        self._position = min(int(position), self._size)
        return self._position

    def read(self, size: int = -1) -> bytes:
        self._checkClosed()
        remaining = self._size - self._position
        requested = remaining if size is None or size < 0 else min(size, remaining)
        if requested <= 0:
            return b""

        start = self._position
        end = start + requested
        observed = self._fetched_size_with(start, end)
        if observed > self._max_bytes:
            raise MemoryLimitExceededError(
                observed_bytes=observed,
                limit_bytes=self._max_bytes,
                path_basename=self._label,
                stage=self._stage,
            )

        payload = self._read_range(start, end - 1)
        if not isinstance(payload, bytes):
            raise TypeError(
                "range reader must return bytes, got "
                f"{type(payload).__name__}"
            )
        if len(payload) > requested:
            raise IOError(
                f"range reader returned {len(payload)} bytes for a "
                f"{requested}-byte request"
            )
        self._position += len(payload)
        self._record_fetched(start, start + len(payload))
        return payload

    def readinto(self, buffer) -> int:
        payload = self.read(len(buffer))
        buffer[:len(payload)] = payload
        return len(payload)

    def _fetched_size_with(self, start: int, end: int) -> int:
        intervals = [*self._fetched, (start, end)]
        intervals.sort()
        total = 0
        current_start, current_end = intervals[0]
        for next_start, next_end in intervals[1:]:
            if next_start <= current_end:
                current_end = max(current_end, next_end)
            else:
                total += current_end - current_start
                current_start, current_end = next_start, next_end
        return total + current_end - current_start

    def _record_fetched(self, start: int, end: int) -> None:
        if end <= start:
            return
        intervals = [*self._fetched, (start, end)]
        intervals.sort()
        merged: list[tuple[int, int]] = []
        for next_start, next_end in intervals:
            if not merged or next_start > merged[-1][1]:
                merged.append((next_start, next_end))
            else:
                old_start, old_end = merged[-1]
                merged[-1] = (old_start, max(old_end, next_end))
        self._fetched = merged


__all__ = ["BoundedRangeReader"]
