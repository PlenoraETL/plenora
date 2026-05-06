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
  - the cap is the dedicated ``ARTIFACT_PREVIEW_MAX_BYTES`` env
    (default 25 MiB, ``Settings.DEFAULTS`` mirror; no
    ``app.config`` import in the reader). Separate from
    ``RESOURCE_MAX_BYTES`` (200 MiB post MEMORY-005) because preview is
    concurrency-sensitive (BE-GO-READY-RUNTIME-001-PREVIEW-MAX-
    BYTES-CAP);
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


def test_default_cap_is_25_mib(monkeypatch):
    """Default cap is 25 MiB when ``ARTIFACT_PREVIEW_MAX_BYTES`` is
    unset. The preview cap is intentionally MUCH lower than
    ``RESOURCE_MAX_BYTES`` (200 MiB post MEMORY-005) because the preview vive sul
    thread-pool API ed e' concorrenza-sensibile."""
    from app.core.intermediate import artifact_reader as ar

    monkeypatch.delenv("ARTIFACT_PREVIEW_MAX_BYTES", raising=False)
    assert ar._resolve_max_bytes() == 25 * 1024 * 1024


def test_env_override_is_honored(monkeypatch):
    """Setting ``ARTIFACT_PREVIEW_MAX_BYTES`` overrides the default."""
    from app.core.intermediate import artifact_reader as ar

    monkeypatch.setenv("ARTIFACT_PREVIEW_MAX_BYTES", str(1024))
    assert ar._resolve_max_bytes() == 1024


def test_invalid_env_falls_back_to_default(monkeypatch):
    """Garbage / non-positive env values fall back to the 25 MiB default."""
    from app.core.intermediate import artifact_reader as ar

    monkeypatch.setenv("ARTIFACT_PREVIEW_MAX_BYTES", "not-an-int")
    assert ar._resolve_max_bytes() == 25 * 1024 * 1024
    monkeypatch.setenv("ARTIFACT_PREVIEW_MAX_BYTES", "0")
    assert ar._resolve_max_bytes() == 25 * 1024 * 1024
    monkeypatch.setenv("ARTIFACT_PREVIEW_MAX_BYTES", "-1")
    assert ar._resolve_max_bytes() == 25 * 1024 * 1024


def test_resource_max_bytes_does_not_drive_preview(monkeypatch):
    """``RESOURCE_MAX_BYTES`` MUST NOT be the env that drives the
    preview cap (that would re-couple the generic blob cap to the
    25 MB preview budget). Setting it should NOT change
    ``_resolve_max_bytes()``."""
    from app.core.intermediate import artifact_reader as ar

    monkeypatch.delenv("ARTIFACT_PREVIEW_MAX_BYTES", raising=False)
    monkeypatch.setenv("RESOURCE_MAX_BYTES", str(1024))
    # Preview cap stays at the dedicated default; RESOURCE_MAX_BYTES
    # is for resource engine blob loaders (result_blob etc.).
    assert ar._resolve_max_bytes() == 25 * 1024 * 1024


def test_settings_default_matches_module_default():
    """``Settings.DEFAULTS["ARTIFACT_PREVIEW_MAX_BYTES"]`` MUST match
    the reader module's ``_DEFAULT_ARTIFACT_PREVIEW_MAX_BYTES``.
    Drift here means the reader and the settings layer disagree
    about the cap, which would surprise operators tuning via env."""
    from app.config import Settings
    from app.core.intermediate import artifact_reader as ar

    assert (
        Settings.DEFAULTS["ARTIFACT_PREVIEW_MAX_BYTES"]
        == ar._DEFAULT_ARTIFACT_PREVIEW_MAX_BYTES
        == 25 * 1024 * 1024
    )


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
    """An artifact larger than ``ARTIFACT_PREVIEW_MAX_BYTES`` MUST
    raise :class:`MemoryLimitExceededError` so the boundary maps to
    HTTP 413 — NOT a best-effort ``ArtifactPreviewResult(error=...)``.
    Otherwise a 50-GB artifact would fill RAM and OOM-kill the
    worker silently."""
    from app.core.intermediate import artifact_reader as ar

    monkeypatch.setenv("ARTIFACT_PREVIEW_MAX_BYTES", "1024")  # 1 KiB cap

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

    monkeypatch.setenv("ARTIFACT_PREVIEW_MAX_BYTES", "1024")  # 1 KiB cap
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


# ---------------------------------------------------------------------------
# 4. Parser-boundary split (BE-GO-READY-RESOURCE-ENGINE-016)
# ---------------------------------------------------------------------------
#
# 016 moved the pandas-bound parsing of the downloaded byte buffer
# into a dedicated leaf module
# (``app/core/intermediate/preview_parser.py``). The reader is now
# a pure I/O orchestrator and contains zero pandas references at
# any indentation level. The tests below lock that boundary at
# three layers: source-bound (no pandas in the orchestrator),
# behavioural (the orchestrator delegates to the parser), and
# regression (oversize still propagates after the split).

from tests2.shared._source_scan import python_code_only  # noqa: E402

_PREVIEW_PARSER_PY = (
    _BACKEND_ROOT / "app" / "core" / "intermediate" / "preview_parser.py"
)
_INTERMEDIATE_PKG = _BACKEND_ROOT / "app" / "core" / "intermediate"


def test_artifact_reader_does_not_reference_pandas():
    """016 contract: ``artifact_reader.py`` is the I/O orchestrator
    and MUST NOT reference pandas at any indentation level. The
    pandas import + DataFrame work was moved to the leaf parser
    module ``preview_parser.py``. Even a lazy ``import pandas``
    inside a function body is a regression -- the orchestrator
    must delegate parsing entirely to the parser leaf."""
    text = _ARTIFACT_READER_PY.read_text(encoding="utf-8")
    code_only = python_code_only(text)
    forbidden_substrings = (
        "import pandas",
        "from pandas",
        # The parser owns every `pd.` usage now; the orchestrator
        # never touches a DataFrame.
        " pd.",
        "(pd.",
    )
    offenders = [s for s in forbidden_substrings if s in code_only]
    assert not offenders, (
        f"app/core/intermediate/artifact_reader.py references pandas "
        f"in executable code: {offenders}. The 016 boundary moved "
        "all pandas usage into app/core/intermediate/preview_parser.py; "
        "the orchestrator must delegate via "
        "`parse_parquet_preview(body_bytes, limit=limit)`."
    )


def test_artifact_reader_no_full_file_pd_read_parquet_pattern():
    """BE-GO-READY-PREVIEW-ENGINE-003 explicit OOM regression lock:
    the canonical OOM-causing pattern
    ``pd.read_parquet(BytesIO(body_bytes))`` MUST NOT reappear in
    ``artifact_reader.py`` under ANY shape. Pre-016 the orchestrator
    materialized the FULL parquet file into a DataFrame before
    trimming with ``df.head(limit)``; that was O(file_rows × ncols)
    RAM regardless of the caller's preview ``limit``.

    Post-016 the orchestrator delegates the bounded byte buffer to
    ``preview_parser.parse_parquet_preview`` which uses
    ``pyarrow.parquet.ParquetFile.iter_batches`` (locked separately
    in ``test_preview_parser_streaming_contract.py``). The
    orchestrator MUST NOT re-introduce a pandas full-file decode
    path even temporarily ("just to handle a fallback"): every
    decode boundary lives in the parser leaf.

    Banned tokens (substring scan over executable code with
    docstrings/comments stripped):
      - ``pd.read_parquet(``: the legacy full-file decode call.
      - ``BytesIO(``: the wrapper used by the pre-016 pattern.
        The orchestrator passes raw ``body_bytes: bytes`` to the
        parser; it MUST NOT wrap them in ``BytesIO`` -- the parser
        owns the ``pa.BufferReader`` zero-copy view.
      - ``import io`` and ``from io``: the orchestrator has no
        legitimate need for the ``io`` module after the W-016
        split; banning the import is defense-in-depth against
        ``BytesIO(body_bytes)`` re-entry.
    """
    text = _ARTIFACT_READER_PY.read_text(encoding="utf-8")
    code_only = python_code_only(text)
    forbidden_substrings = (
        "pd.read_parquet(",
        "BytesIO(",
        "import io",
        "from io ",
    )
    offenders = [s for s in forbidden_substrings if s in code_only]
    assert not offenders, (
        f"app/core/intermediate/artifact_reader.py contains the "
        f"OOM-prone full-file decode pattern in executable code: "
        f"{offenders}. The W-016 / W-018 boundary requires every "
        "parquet decode to live in `preview_parser.py` via "
        "`pyarrow.parquet.ParquetFile.iter_batches` (bounded by "
        "`limit`). Re-introducing `pd.read_parquet(BytesIO(...))` "
        "here would silently restore O(file_rows × ncols) RAM "
        "cost regardless of the caller's preview limit, defeating "
        "the W-018 streaming contract. Delegate to "
        "`preview_parser.parse_parquet_preview(body_bytes, "
        "limit=limit)` and let the parser own the buffer."
    )


def test_artifact_reader_no_temp_file_fallback():
    """BE-GO-READY-PREVIEW-ENGINE-003 'no temp file fallback' lock:
    the orchestrator MUST NOT materialize the bounded byte buffer
    to disk before handing it to the parser. The pre-W-016 design
    space included a "write to tempfile then pyarrow.read_table(path)"
    fallback that would have:

      - doubled the working-set memory (bytes in RAM PLUS a
        tempfile flush);
      - leaked tempfiles on parser exception (the parser raises
        ``ParquetPreviewParseError`` on bad bytes; a tempfile-
        based path needs explicit unlink-on-error scaffolding);
      - re-introduced an OS-tmpdir quota dependency on the
        preview surface.

    The W-018 streaming primitive (pyarrow ``iter_batches`` over
    ``pa.BufferReader``) makes the tempfile path unnecessary
    even for very large files (``ARTIFACT_PREVIEW_MAX_BYTES`` =
    25 MiB caps it upstream). Banning the tempfile primitives at
    source level prevents a future contributor from re-adding
    the path "for safety" without tripping CI.

    Banned tokens (substring scan over executable code):
      - ``tempfile.``: any use of the stdlib tempfile module
        (``NamedTemporaryFile``, ``mkdtemp``, ``TemporaryDirectory``,
        ``mkstemp``).
      - ``import tempfile`` / ``from tempfile``: the orchestrator
        has no legitimate need for the module after W-018.
    """
    text = _ARTIFACT_READER_PY.read_text(encoding="utf-8")
    code_only = python_code_only(text)
    forbidden_substrings = (
        "tempfile.",
        "import tempfile",
        "from tempfile",
    )
    offenders = [s for s in forbidden_substrings if s in code_only]
    assert not offenders, (
        f"app/core/intermediate/artifact_reader.py uses the "
        f"tempfile module in executable code: {offenders}. The "
        "W-018 streaming primitive (`pyarrow.parquet.ParquetFile` "
        "+ `iter_batches`) makes a temp-file fallback unnecessary; "
        "re-introducing one would double working-set memory + "
        "leak temps on parser exception. Pass `body_bytes: bytes` "
        "directly to `preview_parser.parse_parquet_preview`."
    )


def test_preview_parser_module_owns_pandas_import_edge():
    """016 contract: the leaf parser module exists and is the
    canonical owner of the pandas import boundary for the
    intermediate preview path."""
    assert _PREVIEW_PARSER_PY.exists(), (
        "app/core/intermediate/preview_parser.py must exist (016 "
        "moved pandas-bound parsing into this leaf module)"
    )
    text = _PREVIEW_PARSER_PY.read_text(encoding="utf-8")
    assert "import pandas" in text, (
        "preview_parser.py must own the pandas import (it is the "
        "single allowed pandas surface for the preview path)"
    )


def test_no_other_intermediate_module_imports_pandas():
    """016 contract: aside from ``preview_parser.py``, no module
    under ``app/core/intermediate/`` may import pandas. The
    pre-016 lazy import inside ``artifact_reader.py`` was the
    last legitimate exception; with that gone, the boundary is a
    single module. (``snapshot.py`` imports pandas inside the
    write path -- intentional and out of scope; this lock targets
    the *read/preview* surface and excludes ``snapshot.py``
    explicitly so it can be tightened in a future stream.)"""
    allowed = {"__init__.py", "preview_parser.py", "snapshot.py"}
    offenders: list[str] = []
    for path in _INTERMEDIATE_PKG.glob("*.py"):
        if path.name in allowed:
            continue
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        code_only = python_code_only(text)
        if "import pandas" in code_only or "from pandas" in code_only:
            offenders.append(path.name)
    assert not offenders, (
        f"Intermediate read-path module(s) {offenders} import pandas. "
        "The 016 boundary requires that pandas live ONLY in "
        "preview_parser.py for the read/preview surface. Move the "
        "pandas-bound work into a leaf parser/helper or delegate via "
        "`preview_parser.parse_parquet_preview`."
    )


def test_artifact_reader_delegates_to_preview_parser(monkeypatch):
    """016 behavioural lock: when the bounded byte buffer reaches
    the parsing step, the orchestrator MUST hand it off to
    ``preview_parser.parse_parquet_preview`` rather than parsing
    inline. We patch the parser symbol AS IMPORTED INTO
    ``artifact_reader`` (the canonical boundary) and assert it is
    called exactly once with the streamed bytes + the caller's
    ``limit``. The parser's return value is propagated verbatim
    into ``ArtifactPreviewResult``."""
    from app.core.intermediate import artifact_reader as ar
    from app.core.intermediate.preview_parser import ParquetPreview

    body = b"PARQUETBODY-PLACEHOLDER"
    fake_storage = _FakeStorage([body])

    captured: list[tuple] = []

    def _spy(body_bytes, *, limit):
        captured.append((body_bytes, limit))
        return ParquetPreview(
            rows=[{"a": 1}, {"a": 2}],
            col_schema={"a": "int64"},
            row_count=42,
        )

    monkeypatch.setenv("ARTIFACT_PREVIEW_MAX_BYTES", "1024")
    # Patch at the orchestrator's import alias so we observe the
    # boundary call exactly as production would dispatch it.
    monkeypatch.setattr(ar, "parse_parquet_preview", _spy)
    monkeypatch.setattr(
        "app.shared.cloud_backends.get_runtime_object_storage",
        lambda: fake_storage,
    )

    result = ar.load_preview_from_artifact(
        "s3://bucket/key/data.parquet", limit=7,
    )

    assert len(captured) == 1, (
        "artifact_reader must delegate to parse_parquet_preview "
        "exactly once per call"
    )
    streamed_bytes, limit = captured[0]
    assert streamed_bytes == body
    assert limit == 7
    # Verbatim propagation of the parser's typed envelope.
    assert result.error is None
    assert result.rows == [{"a": 1}, {"a": 2}]
    assert result.col_schema == {"a": "int64"}
    assert result.row_count == 42


def test_artifact_reader_surfaces_parser_read_parquet_failure(monkeypatch):
    """016 envelope contract: a parser ``read_parquet`` failure
    surfaces as ``ArtifactPreviewResult(error='read_parquet
    fallito: ...')`` -- byte-for-byte the same operator string the
    pre-016 inline parsing produced. The envelope is the public
    contract; router/UI consumers must not see a behaviour change."""
    from app.core.intermediate import artifact_reader as ar
    from app.core.intermediate.preview_parser import (
        ParquetPreviewParseError,
    )

    fake_storage = _FakeStorage([b"not-parquet"])

    def _raising_parser(body_bytes, *, limit):
        raise ParquetPreviewParseError(
            stage="read_parquet",
            cause=ValueError("Invalid Parquet header"),
        )

    monkeypatch.setenv("ARTIFACT_PREVIEW_MAX_BYTES", "1024")
    monkeypatch.setattr(ar, "parse_parquet_preview", _raising_parser)
    monkeypatch.setattr(
        "app.shared.cloud_backends.get_runtime_object_storage",
        lambda: fake_storage,
    )

    result = ar.load_preview_from_artifact(
        "s3://bucket/key/data.parquet", limit=10,
    )

    assert result.rows == []
    assert result.col_schema == {}
    assert result.row_count == 0
    assert result.error is not None
    # Verbatim string contract: pre-016 router consumers grepped
    # for this prefix; the boundary split must not change it.
    assert result.error.startswith("read_parquet fallito:")
    assert "ValueError" in result.error
    assert "Invalid Parquet header" in result.error


def test_artifact_reader_surfaces_parser_to_dict_failure_with_partial_schema(monkeypatch):
    """016 envelope contract: a parser ``to_dict`` failure surfaces
    as ``ArtifactPreviewResult`` with the PARTIAL schema +
    row_count populated and the ``to_dict fallito: ...`` error
    string. The pre-016 inline path also preserved partial
    metadata; the boundary split keeps that property end-to-end."""
    from app.core.intermediate import artifact_reader as ar
    from app.core.intermediate.preview_parser import (
        ParquetPreviewParseError,
    )

    fake_storage = _FakeStorage([b"x"])

    def _raising_parser(body_bytes, *, limit):
        raise ParquetPreviewParseError(
            stage="to_dict",
            cause=TypeError("unhashable nested column"),
            partial_schema={"col_a": "object", "col_b": "int64"},
            partial_row_count=1234,
        )

    monkeypatch.setenv("ARTIFACT_PREVIEW_MAX_BYTES", "1024")
    monkeypatch.setattr(ar, "parse_parquet_preview", _raising_parser)
    monkeypatch.setattr(
        "app.shared.cloud_backends.get_runtime_object_storage",
        lambda: fake_storage,
    )

    result = ar.load_preview_from_artifact(
        "s3://bucket/key/data.parquet", limit=10,
    )

    assert result.error is not None
    assert result.error.startswith("to_dict fallito:")
    assert "TypeError" in result.error
    # Partial metadata preserved through the envelope.
    assert result.col_schema == {"col_a": "object", "col_b": "int64"}
    assert result.row_count == 1234
    assert result.rows == []


def test_oversize_still_propagates_after_parser_split(monkeypatch):
    """016 regression cover: the boundary split must not weaken the
    012 cap. Oversize MUST still raise
    :class:`MemoryLimitExceededError` -- it is NOT degraded to
    ``error=...``. The parser is never reached on the oversize
    path; we patch it as a tripwire to prove that."""
    from app.core.intermediate import artifact_reader as ar

    monkeypatch.setenv("ARTIFACT_PREVIEW_MAX_BYTES", "100")  # 100 B cap
    chunks = [b"x" * 60, b"y" * 60]  # 120 B -> oversize
    fake_storage = _FakeStorage(chunks)

    parser_calls: list[tuple] = []

    def _tripwire(body_bytes, *, limit):
        parser_calls.append((body_bytes, limit))
        raise AssertionError(
            "parser must NOT be called on the oversize path"
        )

    monkeypatch.setattr(ar, "parse_parquet_preview", _tripwire)
    monkeypatch.setattr(
        "app.shared.cloud_backends.get_runtime_object_storage",
        lambda: fake_storage,
    )

    with pytest.raises(MemoryLimitExceededError) as exc_info:
        ar.load_preview_from_artifact(
            "s3://bucket/key/data.parquet", limit=10,
        )

    assert exc_info.value.stage == "intermediate_artifact_preview"
    assert parser_calls == [], (
        "the parser must remain unreached on oversize; the bounded "
        "read aborts BEFORE handing bytes to the parser"
    )


# ---------------------------------------------------------------------------
# 5. Leaf parser unit-level (BE-GO-READY-RESOURCE-ENGINE-016)
# ---------------------------------------------------------------------------


def test_parser_raises_typed_error_on_bad_bytes():
    """The parser surface is bytes-in, ``ParquetPreview``-out, with
    ``ParquetPreviewParseError`` on decode failure. A non-Parquet
    buffer must raise ``stage='read_parquet'`` with empty partial
    fields (no schema available pre-decode)."""
    from app.core.intermediate.preview_parser import (
        ParquetPreviewParseError,
        parse_parquet_preview,
    )

    with pytest.raises(ParquetPreviewParseError) as exc_info:
        parse_parquet_preview(b"definitely-not-parquet", limit=10)

    err = exc_info.value
    assert err.stage == "read_parquet"
    assert err.partial_schema == {}
    assert err.partial_row_count == 0
    assert err.cause is not None


def test_parser_round_trips_a_real_parquet_buffer(tmp_path):
    """Sanity: a real Parquet buffer (built via pandas in the test)
    round-trips through the parser. Also pins ``row_count`` to the
    full DataFrame size, NOT the head-truncated size, matching the
    pre-016 behaviour."""
    import pandas as pd
    from app.core.intermediate.preview_parser import parse_parquet_preview

    df = pd.DataFrame({"a": list(range(10)), "b": ["x"] * 10})
    parquet_path = tmp_path / "fixture.parquet"
    df.to_parquet(parquet_path)
    body = parquet_path.read_bytes()

    preview = parse_parquet_preview(body, limit=3)

    assert preview.row_count == 10, (
        "row_count must be the FULL parquet size, not the head limit"
    )
    assert len(preview.rows) == 3
    assert preview.rows == [
        {"a": 0, "b": "x"},
        {"a": 1, "b": "x"},
        {"a": 2, "b": "x"},
    ]
    assert set(preview.col_schema.keys()) == {"a", "b"}
