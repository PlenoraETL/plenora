"""HTTP streaming primitives with explicit ownership and completion tracking."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import Any, Callable, Optional
import time
import unicodedata

from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse


class CompletionTrackingIterator(Iterator[bytes]):
    """Track a complete EOF while retaining ownership of the inner iterator."""

    def __init__(
        self,
        body: Iterable[bytes],
        *,
        expected_size: Optional[int] = None,
        on_progress: Callable[[int], None] | None = None,
        progress_interval_seconds: float = 60.0,
    ) -> None:
        self._iterator = iter(body)
        self.expected_size = expected_size if expected_size and expected_size > 0 else None
        self.bytes_yielded = 0
        self.completed = False
        self.closed = False
        self._on_progress = on_progress
        self._progress_interval_seconds = progress_interval_seconds
        self._last_progress_at = time.monotonic()

    def __iter__(self) -> "CompletionTrackingIterator":
        return self

    def __next__(self) -> bytes:
        if self.closed:
            raise StopIteration
        try:
            chunk = next(self._iterator)
        except StopIteration:
            self.completed = (
                self.expected_size is None
                or self.bytes_yielded == self.expected_size
            )
            self.close()
            raise
        except BaseException:
            self.close()
            raise
        next_total = self.bytes_yielded + len(chunk)
        now = time.monotonic()
        if (
            self._on_progress is not None
            and now - self._last_progress_at >= self._progress_interval_seconds
        ):
            try:
                self._on_progress(next_total)
            except BaseException:
                self.close()
                raise
            self._last_progress_at = now
        self.bytes_yielded = next_total
        return chunk

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        close = getattr(self._iterator, "close", None)
        if callable(close):
            close()


class CloseableStreamingResponse(StreamingResponse):
    """StreamingResponse that closes its owned iterator on every ASGI exit."""

    def __init__(
        self,
        content: Any,
        *args: Any,
        on_close: Callable[[], None] | None = None,
        **kwargs: Any,
    ) -> None:
        self.owned_content = content
        self._on_close = on_close
        self._closed = False
        super().__init__(content, *args, **kwargs)

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await run_in_threadpool(self.close)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            close = getattr(self.owned_content, "close", None)
            if callable(close):
                close()
        finally:
            if self._on_close is not None:
                self._on_close()


def safe_download_filename(filename: object, *, fallback: str = "download") -> str:
    """Return a basename safe for a quoted Content-Disposition value."""
    basename = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    basename = unicodedata.normalize("NFKD", basename).encode(
        "ascii", "ignore"
    ).decode("ascii")
    safe = "".join(
        char
        for char in basename
        if char.isprintable() and char not in {'"', "\r", "\n"}
    )[:200]
    return safe or fallback


def close_stream_body(opened: Mapping[str, object]) -> None:
    close = getattr(opened.get("body"), "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            # Cleanup best-effort: callers invoke this immediately before
            # raising the primary validation/size error.
            pass


__all__ = [
    "CloseableStreamingResponse",
    "CompletionTrackingIterator",
    "close_stream_body",
    "safe_download_filename",
]
