from __future__ import annotations

import pytest

from app.core.io import http_streaming
from app.core.io.http_streaming import (
    CloseableStreamingResponse,
    CompletionTrackingIterator,
    safe_download_filename,
)


class _CloseableChunks:
    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._chunks)

    def close(self):
        self.closed = True


def test_completion_requires_eof_and_expected_size():
    source = _CloseableChunks([b"abc", b"def"])
    tracked = CompletionTrackingIterator(source, expected_size=6)
    assert b"".join(tracked) == b"abcdef"
    assert tracked.completed is True
    assert source.closed is True


def test_partial_response_close_preserves_incomplete_state_and_is_idempotent():
    source = _CloseableChunks([b"abc", b"def"])
    finalized = []
    tracked = CompletionTrackingIterator(source, expected_size=6)
    response = CloseableStreamingResponse(
        tracked, on_close=lambda: finalized.append(tracked.completed),
    )
    assert next(tracked) == b"abc"
    response.close()
    response.close()
    assert tracked.completed is False
    assert source.closed is True
    assert finalized == [False]


def test_size_mismatch_is_not_complete_even_after_eof():
    tracked = CompletionTrackingIterator([b"abc"], expected_size=4)
    assert list(tracked) == [b"abc"]
    assert tracked.completed is False


def test_safe_download_filename_removes_path_and_header_delimiters():
    assert safe_download_filename('../../evil\r\n".csv') == "evil.csv"


def test_safe_download_filename_is_latin1_header_safe():
    assert safe_download_filename("citta-東京.csv") == "citta-.csv"


def test_progress_callback_runs_after_interval(monkeypatch):
    clock = iter((0.0, 61.0, 62.0))
    monkeypatch.setattr(http_streaming.time, "monotonic", lambda: next(clock))
    progress = []
    tracked = CompletionTrackingIterator(
        [b"abc", b"def"],
        on_progress=progress.append,
        progress_interval_seconds=60,
    )

    assert list(tracked) == [b"abc", b"def"]
    assert progress == [3]


def test_progress_failure_aborts_and_closes_source(monkeypatch):
    clock = iter((0.0, 61.0))
    monkeypatch.setattr(http_streaming.time, "monotonic", lambda: next(clock))
    source = _CloseableChunks([b"abc"])
    tracked = CompletionTrackingIterator(
        source,
        on_progress=lambda _size: (_ for _ in ()).throw(RuntimeError("lost")),
        progress_interval_seconds=60,
    )

    with pytest.raises(RuntimeError, match="lost"):
        next(tracked)

    assert tracked.bytes_yielded == 0
    assert source.closed is True
