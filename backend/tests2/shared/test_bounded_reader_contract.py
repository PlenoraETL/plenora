"""Contract test for ``read_stream_bounded``.

BE-GO-READY-RESOURCE-ENGINE-009-BOUNDED-STREAM-READER.

Locks the single shared bounded reader's invariants:

  1. Under-limit body returns the joined bytes.
  2. Body whose total size equals ``max_bytes`` succeeds.
  3. Body that would exceed ``max_bytes`` raises
     ``MemoryLimitExceededError`` BEFORE the iterator is fully
     consumed (memory bound preserved).
  4. Non-bytes-like chunk fails clearly with ``TypeError``,
     identifying the chunk's index and observed type.
  5. ``max_bytes < 0`` raises ``ValueError`` at call time.
  6. Empty body returns ``b""``.
  7. Bytes-like alternatives (``bytearray``, ``memoryview``)
     are accepted and normalized to ``bytes``.
  8. Anti-god-engine: the helper module imports stdlib + the
     existing typed exception only — no FastAPI / DB / config /
     boto3 / requests / pandas / GIS dependencies.

Tests are stdlib-only (`pytest` + `unittest.mock`).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator, List

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_ROOT = _REPO_ROOT / "backend"
_HELPER_PY = (
    _BACKEND_ROOT / "app" / "core" / "resources" / "bounded_reader.py"
)


# ---------------------------------------------------------------------------
# 1. Under-limit body returns joined bytes
# ---------------------------------------------------------------------------


def test_under_limit_returns_joined_bytes():
    from app.core.resources.bounded_reader import read_stream_bounded

    chunks = [b"hello ", b"world"]
    out = read_stream_bounded(iter(chunks), max_bytes=1024, label="t-1")
    assert out == b"hello world"
    assert isinstance(out, bytes)


def test_empty_body_returns_empty_bytes():
    from app.core.resources.bounded_reader import read_stream_bounded

    out = read_stream_bounded(iter([]), max_bytes=10, label="empty")
    assert out == b""


def test_single_chunk_works():
    from app.core.resources.bounded_reader import read_stream_bounded

    out = read_stream_bounded(iter([b"x" * 100]), max_bytes=200, label="x")
    assert out == b"x" * 100


# ---------------------------------------------------------------------------
# 2. Exact-limit body succeeds
# ---------------------------------------------------------------------------


def test_exact_limit_body_succeeds():
    """Cumulative bytes EQUAL to max_bytes is OK (inclusive
    bound). Off-by-one regression guard."""
    from app.core.resources.bounded_reader import read_stream_bounded

    out = read_stream_bounded(
        iter([b"a" * 5, b"b" * 5]),
        max_bytes=10,
        label="exact",
    )
    assert out == b"aaaaabbbbb"
    assert len(out) == 10


def test_max_bytes_zero_accepts_empty_body_only():
    from app.core.resources.bounded_reader import read_stream_bounded
    from app.shared.exceptions import MemoryLimitExceededError

    # Empty body with cap 0 → b"" (success).
    out = read_stream_bounded(iter([]), max_bytes=0, label="zero")
    assert out == b""

    # Non-empty body with cap 0 → raise.
    with pytest.raises(MemoryLimitExceededError):
        read_stream_bounded(iter([b"x"]), max_bytes=0, label="zero")


# ---------------------------------------------------------------------------
# 3. Over-limit fails BEFORE the iterator is fully consumed
# ---------------------------------------------------------------------------


def test_over_limit_raises_memory_limit_exceeded():
    from app.core.resources.bounded_reader import read_stream_bounded
    from app.shared.exceptions import MemoryLimitExceededError

    chunks = [b"a" * 5, b"b" * 5, b"c" * 5]
    with pytest.raises(MemoryLimitExceededError) as exc:
        read_stream_bounded(iter(chunks), max_bytes=10, label="over")

    # observed_bytes = running (10) + len(triggering chunk c*5) = 15.
    err = exc.value
    assert err.observed_bytes == 15
    assert err.limit_bytes == 10
    assert err.path_basename == "over"
    assert err.stage == "read_stream_bounded"


def test_over_limit_does_not_consume_remaining_iterator():
    """Memory bound: when the cap fires, the iterator must NOT
    be fully drained. The remaining chunks stay un-yielded."""
    from app.core.resources.bounded_reader import read_stream_bounded
    from app.shared.exceptions import MemoryLimitExceededError

    yielded: List[int] = []

    def _instrumented_body() -> Iterator[bytes]:
        for i in range(10):
            yielded.append(i)
            yield b"x" * 100

    with pytest.raises(MemoryLimitExceededError):
        # Cap of 250 fires on chunk #3 (running 200 + chunk 100 = 300 > 250).
        read_stream_bounded(_instrumented_body(), max_bytes=250, label="bound")

    # The instrumentation must NOT have reached chunk #4 onward.
    # Chunks 0, 1, 2 were yielded (running totals 100, 200, 300).
    # Chunk #3 is the one that triggered the raise; the body
    # generator records the yield in `yielded` BEFORE the helper
    # observes the chunk, so `yielded[-1]` is the index of the
    # over-limit chunk. Anything past that means the helper
    # kept consuming after raising — a memory-bound regression.
    assert yielded[-1] <= 3, (
        f"helper consumed past the over-limit chunk: yielded={yielded}"
    )


def test_first_chunk_alone_exceeds_limit_raises_immediately():
    """A single chunk larger than `max_bytes` must raise on the
    FIRST iteration, not after some accumulation. observed_bytes
    must equal `0 + len(chunk)`."""
    from app.core.resources.bounded_reader import read_stream_bounded
    from app.shared.exceptions import MemoryLimitExceededError

    yielded: List[int] = []

    def _body() -> Iterator[bytes]:
        yielded.append(0)
        yield b"y" * 1000
        yielded.append(1)
        yield b"never-reached"

    with pytest.raises(MemoryLimitExceededError) as exc:
        read_stream_bounded(_body(), max_bytes=100, label="big-first")

    assert yielded == [0], (
        "second chunk must NOT be yielded once the first triggers the cap"
    )
    assert exc.value.observed_bytes == 1000
    assert exc.value.limit_bytes == 100


# ---------------------------------------------------------------------------
# 4. Invalid chunk types fail clearly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_chunk,bad_type_name",
    [
        (None, "NoneType"),
        ("string-not-bytes", "str"),
        (42, "int"),
        ([b"a", b"b"], "list"),
        ({"k": "v"}, "dict"),
    ],
    ids=["none", "str", "int", "list", "dict"],
)
def test_non_bytes_like_chunk_raises_type_error(bad_chunk, bad_type_name):
    from app.core.resources.bounded_reader import read_stream_bounded

    chunks = [b"good", bad_chunk, b"unreached"]
    with pytest.raises(TypeError) as exc:
        read_stream_bounded(iter(chunks), max_bytes=1024, label="type-test")

    msg = str(exc.value)
    assert bad_type_name in msg, (
        f"TypeError must mention the observed type {bad_type_name!r}; "
        f"got: {msg!r}"
    )
    assert "type-test" in msg, "TypeError must mention the label"


def test_non_bytes_like_chunk_does_not_consume_remaining_iterator():
    from app.core.resources.bounded_reader import read_stream_bounded

    yielded: List[int] = []

    def _body() -> Iterator:
        yielded.append(0)
        yield b"good"
        yielded.append(1)
        yield "not-bytes"  # type: ignore[misc]
        yielded.append(2)
        yield b"unreached"

    with pytest.raises(TypeError):
        read_stream_bounded(_body(), max_bytes=1024, label="abort")

    assert yielded == [0, 1], (
        f"helper must abort on the bad chunk and not pull subsequent "
        f"chunks; yielded={yielded}"
    )


# ---------------------------------------------------------------------------
# 5. max_bytes < 0 fails at call time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_max", [-1, -100])
def test_negative_max_bytes_raises_value_error(bad_max: int):
    from app.core.resources.bounded_reader import read_stream_bounded

    with pytest.raises(ValueError, match="max_bytes must be >= 0"):
        read_stream_bounded(iter([b"x"]), max_bytes=bad_max, label="neg")


# ---------------------------------------------------------------------------
# 6. Bytes-like alternatives (bytearray, memoryview) are accepted
# ---------------------------------------------------------------------------


def test_bytearray_chunks_accepted():
    from app.core.resources.bounded_reader import read_stream_bounded

    chunks = [bytearray(b"alpha "), bytearray(b"beta")]
    out = read_stream_bounded(iter(chunks), max_bytes=1024, label="ba")
    assert out == b"alpha beta"
    assert isinstance(out, bytes)


def test_memoryview_chunks_accepted():
    from app.core.resources.bounded_reader import read_stream_bounded

    base = b"streaming-payload"
    chunks = [memoryview(base[:9]), memoryview(base[9:])]
    out = read_stream_bounded(iter(chunks), max_bytes=1024, label="mv")
    assert out == base


# ---------------------------------------------------------------------------
# 7. Anti-god-engine: helper module is stdlib-only (+ typed exception)
# ---------------------------------------------------------------------------


_FORBIDDEN_TOPLEVEL_IMPORTS = {
    # Frameworks
    "fastapi",
    "starlette",
    # DB / Redis
    "psycopg2",
    "redis",
    "app.db",
    # Settings / config
    "app.config",
    # HTTP / cloud SDKs
    "boto3",
    "requests",
    "app.shared.cloud_backends",
    # Heavy data
    "pandas",
    "geopandas",
    "fiona",
    "polars",
}


def test_bounded_reader_module_is_stdlib_only():
    """The helper module is part of the resource engine's
    anti-god-engine perimeter. It must import stdlib + the
    existing typed `MemoryLimitExceededError` only.

    Source-bound: parse the AST to find every top-level import
    and assert none falls in the forbidden set."""
    text = _HELPER_PY.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(_HELPER_PY))

    top_level_imports: list[tuple[int, str]] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            top_level_imports.append((node.lineno, mod))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top_level_imports.append((node.lineno, alias.name))

    violations: list[tuple[int, str]] = []
    for lineno, mod in top_level_imports:
        for forbidden in _FORBIDDEN_TOPLEVEL_IMPORTS:
            if mod == forbidden or mod.startswith(forbidden + "."):
                violations.append((lineno, mod))
                break

    assert violations == [], (
        f"bounded_reader.py introduced forbidden import(s): {violations}. "
        "The helper must stay stdlib-only (+ MemoryLimitExceededError)."
    )


def test_bounded_reader_does_not_use_list_body_pattern():
    """Source-bound: the helper must NOT use `list(body)` or
    similar fully-eager materialization patterns. The whole
    point of the helper is to avoid those."""
    text = _HELPER_PY.read_text(encoding="utf-8")
    # Strip docstrings so docstring mentions of `list(body)` (as
    # a description of what the helper avoids) don't trip the
    # check.
    tree = ast.parse(text, filename=str(_HELPER_PY))

    bad_patterns = ("list(body)", 'b"".join(body)', "b''.join(body)")
    body_src = ""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "read_stream_bounded":
            body_src = ast.unparse(node)
            break

    assert body_src, "read_stream_bounded function not found"
    for pattern in bad_patterns:
        assert pattern not in body_src, (
            f"read_stream_bounded body contains forbidden pattern "
            f"{pattern!r} — that defeats the bounded-read invariant."
        )
