"""KEEP_COMPAT wrapper for file-source read orchestration.

Canonical implementation lives in `app.core.files.source_read`. This module
remains importable for legacy callers and test monkeypatch surfaces.
"""

from __future__ import annotations

import sys as _sys

from app.core.files import source_read as _canonical

_sys.modules[__name__] = _canonical
