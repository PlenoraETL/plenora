"""Bounded-preview contract for ``app.core.intermediate.artifact_reader``.

BE-GO-READY-RESOURCE-ENGINE-012-INTERMEDIATE-ARTIFACT-BOUNDED-READ.

Locks the intermediate artifact preview cap:

  - object-storage reads are bounded by ``_resolve_max_bytes()``;
    iteration stops AT the first cap-exceeding chunk (no
    ``b"".join(stream["body"])`` materialization);
  - exceeding the cap raises :class:`MemoryLimitExceededError`
    rather than returning ``ArtifactPreviewResult(error=...)`` —
    oversize is a typed/controlled resource cap signal, NOT a
    best-effort connectivity error;
  - the cap mirrors ``RESOURCE_MAX_BYTES`` env (default 500 MiB,
    ``Settings.DEFAULTS`` mirror; no ``app.config`` import in the
    reader);
  - connectivity / parser / schema failures continue to flow
    through the existing best-effort ``error=...`` channel.

Source-bound check: the reader's source MUST NOT contain the
inline unbounded ``b"".join(stream["body"])`` /
``list(stream["body"])`` pattern. This guard is independent of
the runtime stream and prevents the pre-012 shape from
re-appearing.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from app.shared.exceptions import MemoryLimitExceededError


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_ARTIFACT_READER_PY = (
    _BACKEND_ROOT / "app" / "core" / "intermediate" / "artifact_reader.py"
)


# ---------------------------------------------------------------------------
# 1. Cap resolution mirror of result_blob loader
# ---------------------------------------------------------------------------


def test_default_cap_is_500_mib(monkeypatch):
    """Default cap is 500 MiB when ``RESOURCE_MAX_BYTES`` is unset."""
    from app.core.intermediate import artifact_reader as ar

    monkeypatch.delenv("RESOURCE_MAX_BYTES", raising=False)
    assert ar._resolve_max_bytes() == 500 * 1024 * 1024


def test_env_override_is_honored(monkeypatch):
    """Setting ``RESOURCE_MAX_BYTES`` overrides the default."""
    from app.core.intermediate import artifact_reader as ar

    monkeypatch.setenv("RESOURCE_MAX_BYTES", str(1024))
    assert ar._resolve_max_bytes() == 1024


def test_invalid_env_falls_back_to_default(monkeypatch):
    """Garbage / non-positive env values fall back to the default."""
    from app.core.intermediate import artifact_reader as ar

    monkeypatch.setenv("RESOURCE_MAX_BYTES", "not-an-int")
    assert ar._resolve_max_bytes() == 500 * 1024 * 1024
    monkeypatch.setenv("RESOURCE_MAX_BYTES", "0")
    assert ar._resolve_max_bytes() == 500 * 1024 * 1024
    monkeypatch.setenv("RESOURCE_MAX_BYTES", "-1")
    assert ar._resolve_max_bytes() == 500 * 1024 * 1024


# ---------------------------------------------------------------------------
# 2. Source-bound: no inline unbounded read pattern
# ---------------------------------------------------------------------------


def test_source_has_no_unbounded_body_join():
    """The reader MUST NOT contain ``b"".join(<expr>["body"])`` or
    ``list(<expr>["body"])``. The bounded read goes through
    ``read_stream_bounded(...)``."""
    text = _ARTIFACT_READER_PY.read_text(encoding="utf-8")
    # Strip docstrings/comments via regex (good enough for this
    # narrow guard; see test_blob_consumer_memory_audit for the AST
    # ratchet that locks the cross-codebase invariant).
    join_pattern = re.compile(r"""b["']\s*["']\s*\.join\s*\([^)]*\["body"\]""")
    list_pattern = re.compile(r"""list\s*\([^)]*\["body"\]""")
    assert not join_pattern.search(text), (
        "artifact_reader.py contains an unbounded `b\"\".join(...[\"body\"])` "
        "pattern; use `read_stream_bounded(stream[\"body\"], ...)` instead."
    )
    assert not list_pattern.search(text), (
        "artifact_reader.py contains a `list(...[\"body\"])` "
        "materialization; use `read_stream_bounded(stream[\"body\"], ...)` "
        "instead."
    )


# ---------------------------------------------------------------------------
# 3. Oversize raises typed exception, not best-effort error
# ---------------------------------------------------------------------------


class _FakeStorage:
    """Minimal object-storage stub: yields chunks from the body."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    def get_object_stream(self, _uri: str) -> dict:
        return {"body": iter(self._chunks)}


def test_oversize_raises_memory_limit_exceeded(monkeypatch):
    """An artifact larger than ``RESOURCE_MAX_BYTES`` MUST raise
    :class:`MemoryLimitExceededError` so the boundary maps to HTTP
    413 — NOT a best-effort ``ArtifactPreviewResult(error=...)``.
    Otherwise a 50-GB artifact would fill RAM and OOM-kill the
    worker silently."""
    from app.core.intermediate import artifact_reader as ar

    monkeypatch.setenv("RESOURCE_MAX_BYTES", "1024")  # 1 KiB cap

    # Two 700 B chunks; second push would exceed 1 KiB.
    chunks = [b"x" * 700, b"y" * 700]
    fake_storage = _FakeStorage(chunks)

    with patch.object(
        ar,
        "load_preview_from_artifact",
        wraps=ar.load_preview_from_artifact,
    ):
        with patch(
            "app.shared.cloud_backends.get_runtime_object_storage",
            return_value=fake_storage,
        ):
            with pytest.raises(MemoryLimitExceededError) as exc_info:
                ar.load_preview_from_artifact(
                    "s3://bucket/key/data.parquet", limit=10,
                )

    err = exc_info.value
    assert err.limit_bytes == 1024
    assert err.observed_bytes == 1400  # 700 (running) + 700 (next chunk)
    assert err.stage == "intermediate_artifact_preview"


def test_under_cap_proceeds_without_raising_oom(monkeypatch):
    """Under-cap reads continue to the parquet decode step. Decode
    will fail (the body is not valid Parquet), so the result MUST
    surface that failure as ``error=...`` — NOT raise
    :class:`MemoryLimitExceededError`. This proves the bounded read
    is transparent under cap and only fires on overrun."""
    from app.core.intermediate import artifact_reader as ar

    monkeypatch.setenv("RESOURCE_MAX_BYTES", "1024")  # 1 KiB cap
    chunks = [b"not-parquet-but-small"]
    fake_storage = _FakeStorage(chunks)

    with patch(
        "app.shared.cloud_backends.get_runtime_object_storage",
        return_value=fake_storage,
    ):
        result = ar.load_preview_from_artifact(
            "s3://bucket/key/data.parquet", limit=10,
        )

    # Did NOT raise. Decode failed -> error surfaced as best-effort.
    assert result.error is not None
    assert "read_parquet" in result.error or "fallito" in result.error
    assert result.rows == []
