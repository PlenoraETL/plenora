"""
Streaming preview contract for

``app.core.intermediate.preview_parser``.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from tests2.shared._ast_identifiers import top_level_imported_modules


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_PREVIEW_PARSER_PY = (
    _BACKEND_ROOT / "app" / "core" / "intermediate" / "preview_parser.py"
)


# Helpers — synthesize parquet bytes via pyarrow (already a hard dep)


def _make_parquet(rows: list[dict] | None, columns: list[str] | None = None) -> bytes:
    """Round-trip helper: build a parquet bytes buffer from a list
    of row dicts. Mirrors the production write path
    (``pd.to_parquet(engine='pyarrow')``) so dtype mappings
    match what ``artifact_writer.py`` would produce."""
    import pandas as pd
    if rows is None and columns is not None:
        df = pd.DataFrame({c: [] for c in columns})
    else:
        df = pd.DataFrame(rows or [])
    buf = io.BytesIO()
    df.to_parquet(buf, engine="pyarrow")
    return buf.getvalue()


# 1. Streaming behavior — limit << total rows


def test_streaming_returns_only_limit_rows_for_large_file():
    """A 1000-row file with limit=10 MUST return exactly 10 rows
    AND row_count=1000. The decode path must not collapse to a
    full-file materialization just because it can fit in memory."""
    from app.core.intermediate.preview_parser import parse_parquet_preview

    rows = [{"a": i, "b": f"s{i}"} for i in range(1000)]
    body = _make_parquet(rows)

    result = parse_parquet_preview(body, limit=10)

    assert result.row_count == 1000
    assert len(result.rows) == 10
    assert result.rows[0] == {"a": 0, "b": "s0"}
    assert result.rows[-1] == {"a": 9, "b": "s9"}
    assert set(result.col_schema.keys()) == {"a", "b"}


def test_limit_greater_than_total_returns_all_rows():
    """If ``limit > num_rows`` the parser returns every row, not
    a padded shape."""
    from app.core.intermediate.preview_parser import parse_parquet_preview

    rows = [{"x": i} for i in range(5)]
    body = _make_parquet(rows)

    result = parse_parquet_preview(body, limit=100)

    assert result.row_count == 5
    assert len(result.rows) == 5
    assert [r["x"] for r in result.rows] == [0, 1, 2, 3, 4]


def test_zero_row_parquet_returns_empty_rows_with_full_schema():
    """A zero-row parquet must surface ``rows=[]`` AND the full
    schema from the footer. The streaming path must not lose
    the schema just because no batches were emitted."""
    from app.core.intermediate.preview_parser import parse_parquet_preview

    body = _make_parquet(None, columns=["alpha", "beta", "gamma"])
    result = parse_parquet_preview(body, limit=10)

    assert result.row_count == 0
    assert result.rows == []
    # The legacy path also returned `object` for empty pandas
    # frames; we lock the column NAMES (the dtype mapping for
    # truly empty frames depends on pandas/pyarrow version
    # contract — the names are the load-bearing surface).
    assert set(result.col_schema.keys()) == {"alpha", "beta", "gamma"}


# 2. Schema fidelity vs the legacy pd.read_parquet path


def test_col_schema_dtype_strings_match_legacy_format():
    """The dtype strings the parser emits MUST match what
    ``pd.read_parquet(BytesIO(body)).dtypes`` would have
    produced pre-018, so router consumers and the FE preview
    column-type renderer don't see a regression."""
    import pandas as pd
    from app.core.intermediate.preview_parser import parse_parquet_preview

    rows = [
        {"i": 1, "s": "x", "f": 1.5, "b": True},
        {"i": 2, "s": "y", "f": 2.5, "b": False},
    ]
    body = _make_parquet(rows)

    legacy_df = pd.read_parquet(io.BytesIO(body), engine="pyarrow")
    legacy_schema = {
        str(c): str(legacy_df[c].dtype) for c in legacy_df.columns
    }

    result = parse_parquet_preview(body, limit=10)
    assert result.col_schema == legacy_schema


# 3. Error envelope — read_parquet stage


def test_invalid_bytes_raises_read_parquet_stage():
    """Garbage bytes must surface ``stage='read_parquet'`` (NOT
    ``stage='to_dict'``) so the upstream error message stays
    ``'read_parquet fallito: ...'``."""
    from app.core.intermediate.preview_parser import (
        ParquetPreviewParseError,
        parse_parquet_preview,
    )

    with pytest.raises(ParquetPreviewParseError) as exc_info:
        parse_parquet_preview(b"this-is-not-a-parquet-file", limit=10)

    assert exc_info.value.stage == "read_parquet"
    # Partial fields must remain empty when the file cannot be
    # opened — the caller surfaces "rows unavailable" with no
    # schema hint.
    assert exc_info.value.partial_schema == {}
    assert exc_info.value.partial_row_count == 0


def test_empty_bytes_raises_read_parquet_stage():
    """Empty bytes is also a read_parquet failure (no footer)."""
    from app.core.intermediate.preview_parser import (
        ParquetPreviewParseError,
        parse_parquet_preview,
    )

    with pytest.raises(ParquetPreviewParseError) as exc_info:
        parse_parquet_preview(b"", limit=10)

    assert exc_info.value.stage == "read_parquet"


# 4. Source-bound: no full-file pd.read_parquet


def test_parser_does_not_call_pd_read_parquet_on_full_buffer():
    """Lock the row-group-streaming primitive: the parser MUST
    use ``pyarrow.parquet.ParquetFile`` + ``iter_batches``, NOT
    ``pd.read_parquet(BytesIO(body_bytes))``. A regression here
    would silently re-introduce O(file_rows) memory cost.

    Source-bound check (substring scan of the module file). The
    parser file must NOT contain ``pd.read_parquet`` and MUST
    contain both ``ParquetFile(`` and ``iter_batches(``."""
    text = _PREVIEW_PARSER_PY.read_text(encoding="utf-8")

    # The pre-018 token: a full-file materialization. Allowed
    # ONLY in test code (which lives elsewhere); production
    # code in this module must not contain it. We strip
    # docstrings/comments via a minimal pass: the token can
    # legitimately appear in a docstring describing the legacy
    # path. We distinguish by requiring the token to NOT appear
    # outside string literals — which is what
    # ``ast`` sees as ``ast.Attribute`` calls. Here a simpler
    # check is enough: the legacy CALL uses ``pd.read_parquet(``
    # with an open paren; the docstring text typically uses
    # ``pd.read_parquet`` without trailing ``(`` in prose. We
    # ban only the function-call shape.
    import re

    # Strip Python string literals so docstring mentions of the
    # NOTE: legacy API do not false-positive.
    code_only = re.sub(r'"""[\s\S]*?"""', "", text)
    code_only = re.sub(r"'''[\s\S]*?'''", "", code_only)

    assert "pd.read_parquet(" not in code_only, (
        "preview_parser.py must NOT call pd.read_parquet on the "
        "full byte buffer; that re-introduces O(file_rows) RAM "
        "cost and undoes BE-GO-READY-RESOURCE-ENGINE-018. "
        "Use pyarrow.parquet.ParquetFile + iter_batches instead."
    )
    assert "ParquetFile(" in code_only, (
        "preview_parser.py must use pyarrow.parquet.ParquetFile "
        "as the row-group streaming primitive."
    )
    assert "iter_batches(" in code_only, (
        "preview_parser.py must use ParquetFile.iter_batches as "
        "the bounded-decode primitive (limit-scoped first batch)."
    )


def test_parser_module_does_not_import_pyarrow_at_module_edge():
    """The pyarrow + pandas imports must remain LAZY (inside
    ``parse_parquet_preview``). A top-level import would force
    every consumer of ``preview_parser`` to pay the import cost
    even when the parser is never invoked, and would defeat the
    boundary contract documented in the module header."""
    text = _PREVIEW_PARSER_PY.read_text(encoding="utf-8")

    # Module-edge imports = direct children of the module body (AST), so a
    # lazy `import pyarrow` inside parse_parquet_preview is correctly allowed
    # and the prose mentioning the contract in the header can't false-trip.
    edge = top_level_imported_modules(text)
    assert "pyarrow" not in edge, (
        "pyarrow must be imported LAZILY inside "
        "parse_parquet_preview, not at the module edge."
    )
    assert "pandas" not in edge, (
        "pandas must be imported LAZILY inside "
        "parse_parquet_preview, not at the module edge."
    )
