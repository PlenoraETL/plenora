"""Shared micro-utilities for Resource Engine loaders.

BE-GO-READY-ARCH-055-W5-RESOURCE-CONTRACT-SIMPLIFICATION.

Currently holds one helper:

  - ``_short_key`` — log-safe abbreviation of long storage keys.
    Both ``result_blob`` and ``file_source_blob`` used identical
    private copies; centralised here to remove the duplication.

Boundary:
  - stdlib only; zero domain imports.
  - private module (leading underscore): not re-exported from
    ``loaders/__init__.py``.
"""

from __future__ import annotations

from typing import Optional

__all__: list[str] = []  # intentionally empty; callers import directly


def _short_key(key: Optional[str], max_len: int = 60) -> str:
    """Abbrevia una chiave per log: prime 12 + '...' + ultimi 30 char.

    Used by both loaders so log line shapes stay consistent across
    kinds (no ops dashboard breakage when switching backends).
    """
    if not key:
        return "<none>"
    if len(key) <= max_len:
        return key
    return f"{key[:12]}...{key[-30:]}"
