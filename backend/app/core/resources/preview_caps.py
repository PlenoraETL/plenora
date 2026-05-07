"""
Shared cap resolver - single source of truth for

``ARTIFACT_PREVIEW_MAX_BYTES``.
"""

from __future__ import annotations

import os as _os
from typing import Mapping

__all__ = [
    "ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT",
    "ARTIFACT_PREVIEW_MAX_BYTES_ENV",
    "resolve_artifact_preview_max_bytes",
]


ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT: int = 25 * 1024 * 1024  # 25 MB
ARTIFACT_PREVIEW_MAX_BYTES_ENV: str = "ARTIFACT_PREVIEW_MAX_BYTES"


def resolve_artifact_preview_max_bytes(
    env: Mapping[str, str] | None = None,
) -> int:
    """Resolve the artifact-preview byte cap.

    Args:
        env: optional mapping with ``str -> str`` semantics.
            Defaults to ``os.environ``. Tests can pass a plain
            ``dict`` for isolation; production reads live env.
            Read at each call so monkeypatch / runtime override
            takes effect without a process restart.

    Returns:
        The cap in bytes. Override via the
        ``ARTIFACT_PREVIEW_MAX_BYTES`` env entry. Invalid
        (non-int), zero, or negative values fall back to
        ``ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT`` (25 MB).
    """
    source = _os.environ if env is None else env
    raw = source.get(ARTIFACT_PREVIEW_MAX_BYTES_ENV)
    if not raw:
        return ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT
    return v if v > 0 else ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT
