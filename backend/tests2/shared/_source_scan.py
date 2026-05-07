"""
Shared source-scanner helpers for source-bound contract tests.

BE-GO-READY-TEST-INFRA-004-CONTRACT-TEST-WALL-REDUCTION.
"""

from __future__ import annotations

import io
import tokenize


_STRIP_TOKEN_TYPES: frozenset[int] = frozenset(
    {
        tokenize.STRING,
        tokenize.COMMENT,
        getattr(tokenize, "FSTRING_START", -1),
        getattr(tokenize, "FSTRING_MIDDLE", -1),
        getattr(tokenize, "FSTRING_END", -1),
    }
)


def python_code_only(source: str) -> str:
    """Return ``source`` with string literals, f-string literals,
    and comments removed. Falls back to raw source on a tokenise
    failure (a malformed file is a real bug; over-reporting is
    safer than silently passing a regression)."""
    out: list[str] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenizeError:
        return source
    for tok in tokens:
        if tok.type in _STRIP_TOKEN_TYPES:
            continue
        out.append(tok.string)
    return " ".join(out)
