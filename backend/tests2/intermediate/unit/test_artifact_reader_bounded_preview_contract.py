"""
Bounded-preview contract for ``app.core.intermediate.artifact_reader``.

BE-GO-READY-RESOURCE-ENGINE-012-INTERMEDIATE-ARTIFACT-BOUNDED-READ.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.resources.exceptions import MemoryLimitExceededError


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_ARTIFACT_READER_PY = (
    _BACKEND_ROOT / "app" / "core" / "intermediate" / "artifact_reader.py"
)


# 1. Cap resolution mirror of result_blob loader


def test_default_cap_is_25_mib(monkeypatch):
    """Default cap is 25 MiB when ``ARTIFACT_PREVIEW_MAX_BYTES`` is
    unset. The preview cap is intentionally MUCH lower than
    ``RESOURCE_MAX_BYTES`` (200 MiB post MEMORY-005) because the preview vive sul
    thread-pool API ed e' concorrenza-sensibile."""
    from app.core.resources.preview_caps import (
        resolve_artifact_preview_max_bytes,
    )

    monkeypatch.delenv("ARTIFACT_PREVIEW_MAX_BYTES", raising=False)
    assert resolve_artifact_preview_max_bytes() == 25 * 1024 * 1024


def test_env_override_is_honored(monkeypatch):
    """Setting ``ARTIFACT_PREVIEW_MAX_BYTES`` overrides the default."""
    from app.core.resources.preview_caps import (
        resolve_artifact_preview_max_bytes,
    )

    monkeypatch.setenv("ARTIFACT_PREVIEW_MAX_BYTES", str(1024))
    assert resolve_artifact_preview_max_bytes() == 1024


@pytest.mark.parametrize("env_value", ["not-an-int", "0", "-1"])
def test_invalid_env_falls_back_to_default(monkeypatch, env_value):
    """Garbage / non-positive env values fall back to the 25 MiB default."""
    from app.core.resources.preview_caps import (
        resolve_artifact_preview_max_bytes,
    )

    monkeypatch.setenv("ARTIFACT_PREVIEW_MAX_BYTES", env_value)
    assert resolve_artifact_preview_max_bytes() == 25 * 1024 * 1024


def test_resource_max_bytes_does_not_drive_preview(monkeypatch):
    """``RESOURCE_MAX_BYTES`` MUST NOT be the env that drives the
    preview cap (that would re-couple the generic blob cap to the
    25 MB preview budget). Setting it should NOT change
    ``resolve_artifact_preview_max_bytes()``."""
    from app.core.resources.preview_caps import (
        resolve_artifact_preview_max_bytes,
    )

    monkeypatch.delenv("ARTIFACT_PREVIEW_MAX_BYTES", raising=False)
    monkeypatch.setenv("RESOURCE_MAX_BYTES", str(1024))
    # Preview cap stays at the dedicated default; RESOURCE_MAX_BYTES
    # is for resource engine blob loaders (result_blob etc.).
    assert resolve_artifact_preview_max_bytes() == 25 * 1024 * 1024


def test_settings_default_matches_module_default():
    """``Settings.DEFAULTS["ARTIFACT_PREVIEW_MAX_BYTES"]`` MUST match
    the canonical resolver default (``preview_caps.ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT``).
    Drift here means the resolver and the settings layer disagree
    about the cap, which would surprise operators tuning via env."""
    from app.config import Settings
    from app.core.resources.preview_caps import (
        ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT,
    )

    assert (
        Settings.DEFAULTS["ARTIFACT_PREVIEW_MAX_BYTES"]
        == ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT
        == 25 * 1024 * 1024
    )


# 2. Source-bound: no inline unbounded read pattern


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


# 3. Oversize raises typed exception, not best-effort error


class _FakeStorage:
    """Minimal object-storage stub: yields chunks from the body."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    def get_object_stream(self, _uri: str) -> dict:
        return {"body": iter(self._chunks)}


def _patch_storage_and_cap(monkeypatch, chunks: list[bytes], cap: int = 1024):
    from app.core.intermediate import artifact_reader as ar

    monkeypatch.setenv("ARTIFACT_PREVIEW_MAX_BYTES", str(cap))
    monkeypatch.setattr(
        "app.core.io.object_storage.get_runtime_object_storage",
        lambda: _FakeStorage(chunks),
    )
    return ar


def test_oversize_raises_memory_limit_exceeded(monkeypatch):
    """An artifact larger than ``ARTIFACT_PREVIEW_MAX_BYTES`` MUST
    raise :class:`MemoryLimitExceededError` so the boundary maps to
    HTTP 413 — NOT a best-effort ``ArtifactPreviewResult(error=...)``.
    Otherwise a 50-GB artifact would fill RAM and OOM-kill the
    worker silently."""
    # Two 700 B chunks; second push would exceed 1 KiB.
    ar = _patch_storage_and_cap(monkeypatch, [b"x" * 700, b"y" * 700])

    with patch.object(
        ar,
        "load_preview_from_artifact",
        wraps=ar.load_preview_from_artifact,
    ):
        with pytest.raises(MemoryLimitExceededError) as exc_info:
            ar.load_preview_from_artifact(
                "intermediate/key/data.parquet",
                expected_group_id="g1", limit=10,
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
    ar = _patch_storage_and_cap(monkeypatch, [b"not-parquet-but-small"])
    result = ar.load_preview_from_artifact(
        "intermediate/key/data.parquet",
        expected_group_id="g1", limit=10,
    )

    # Did NOT raise. Decode failed -> error surfaced as best-effort.
    assert result.error is not None
    assert "read_parquet" in result.error or "fallito" in result.error
    assert result.rows == []


# 4. Parser-boundary split (BE-GO-READY-RESOURCE-ENGINE-016)
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
    """016/step-2 contract: the leaf parser module exists, but the
    concrete dataframe engine stays behind app.core.dataframes."""
    assert _PREVIEW_PARSER_PY.exists(), (
        "app/core/intermediate/preview_parser.py must exist (016 "
        "moved parquet preview parsing into this leaf module)"
    )
    text = _PREVIEW_PARSER_PY.read_text(encoding="utf-8")
    code_only = python_code_only(text)
    assert "import pandas" not in code_only and "from pandas" not in code_only, (
        "preview_parser.py must not import pandas directly; dataframe "
        "conversion belongs to app.core.dataframes."
    )
    assert "from app.core.dataframes.tabular import" in text, (
        "preview_parser.py must delegate dataframe preview materialization "
        "through app.core.dataframes.tabular."
    )


def test_no_other_intermediate_module_imports_pandas():
    """Step-2 contract: no intermediate module imports pandas directly."""
    allowed = {"__init__.py"}
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
        "Step 2 requires pandas to live behind app.core.dataframes; "
        "delegate dataframe-bound work through the dataframe boundary."
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
    from app.core.intermediate.preview_parser import ParquetPreview

    body = b"PARQUETBODY-PLACEHOLDER"
    ar = _patch_storage_and_cap(monkeypatch, [body])

    captured: list[tuple] = []

    def _spy(body_bytes, *, limit):
        captured.append((body_bytes, limit))
        return ParquetPreview(
            rows=[{"a": 1}, {"a": 2}],
            col_schema={"a": "int64"},
            row_count=42,
        )

    # Patch at the orchestrator's import alias so we observe the
    # boundary call exactly as production would dispatch it.
    monkeypatch.setattr(ar, "parse_parquet_preview", _spy)

    result = ar.load_preview_from_artifact(
        "intermediate/key/data.parquet",
        expected_group_id="g1", limit=7,
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
    from app.core.intermediate.preview_parser import (
        ParquetPreviewParseError,
    )

    ar = _patch_storage_and_cap(monkeypatch, [b"not-parquet"])

    def _raising_parser(body_bytes, *, limit):
        raise ParquetPreviewParseError(
            stage="read_parquet",
            cause=ValueError("Invalid Parquet header"),
        )

    monkeypatch.setattr(ar, "parse_parquet_preview", _raising_parser)

    result = ar.load_preview_from_artifact(
        "intermediate/key/data.parquet",
        expected_group_id="g1", limit=10,
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
    from app.core.intermediate.preview_parser import (
        ParquetPreviewParseError,
    )

    ar = _patch_storage_and_cap(monkeypatch, [b"x"])

    def _raising_parser(body_bytes, *, limit):
        raise ParquetPreviewParseError(
            stage="to_dict",
            cause=TypeError("unhashable nested column"),
            partial_schema={"col_a": "object", "col_b": "int64"},
            partial_row_count=1234,
        )

    monkeypatch.setattr(ar, "parse_parquet_preview", _raising_parser)

    result = ar.load_preview_from_artifact(
        "intermediate/key/data.parquet",
        expected_group_id="g1", limit=10,
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
    ar = _patch_storage_and_cap(
        monkeypatch,
        [b"x" * 60, b"y" * 60],
        cap=100,
    )

    parser_calls: list[tuple] = []

    def _tripwire(body_bytes, *, limit):
        parser_calls.append((body_bytes, limit))
        raise AssertionError(
            "parser must NOT be called on the oversize path"
        )

    monkeypatch.setattr(ar, "parse_parquet_preview", _tripwire)

    with pytest.raises(MemoryLimitExceededError) as exc_info:
        ar.load_preview_from_artifact(
            "intermediate/key/data.parquet",
            expected_group_id="g1", limit=10,
        )

    assert exc_info.value.stage == "intermediate_artifact_preview"
    assert parser_calls == [], (
        "the parser must remain unreached on oversize; the bounded "
        "read aborts BEFORE handing bytes to the parser"
    )
