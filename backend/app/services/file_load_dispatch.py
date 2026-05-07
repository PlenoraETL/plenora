"""Compatibility surface for file parser dispatch.

Canonical implementation: `app.core.files.load_dispatch`.
"""

from __future__ import annotations

from app.core.files.load_dispatch import (
    DataLoaderProtocol,
    LoggerProtocol,
    load_by_format,
)


__all__ = ["DataLoaderProtocol", "LoggerProtocol", "load_by_format"]
