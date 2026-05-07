"""
Shared cap resolver — single source of truth for

``RESOURCE_MAX_BYTES``.
"""

from __future__ import annotations

import os as _os
from typing import Mapping

__all__ = [
    "RESOURCE_MAX_BYTES_DEFAULT",
    "RESOURCE_MAX_BYTES_ENV",
    "resolve_resource_max_bytes",
]


RESOURCE_MAX_BYTES_DEFAULT: int = 200 * 1024 * 1024  # 200 MB
RESOURCE_MAX_BYTES_ENV: str = "RESOURCE_MAX_BYTES"


def resolve_resource_max_bytes(
    env: Mapping[str, str] | None = None,
) -> int:
    """Resolve the resource-blob byte cap.

    Args:
        env: optional mapping with ``str -> str`` semantics.
            Defaults to ``os.environ``. Tests can pass a plain
            ``dict`` for isolation; production reads live env.
            Read at each call so monkeypatch / runtime override
            takes effect without a process restart.

    Returns:
        The cap in bytes. Override via the ``RESOURCE_MAX_BYTES``
        env entry. Invalid (non-int), zero, or negative values
        fall back to ``RESOURCE_MAX_BYTES_DEFAULT`` (200 MB).
    """
    source = _os.environ if env is None else env
    raw = source.get(RESOURCE_MAX_BYTES_ENV)
    if not raw:
        return RESOURCE_MAX_BYTES_DEFAULT
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return RESOURCE_MAX_BYTES_DEFAULT
    return v if v > 0 else RESOURCE_MAX_BYTES_DEFAULT
