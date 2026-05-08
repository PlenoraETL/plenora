"""KEEP_COMPAT wrapper for temporary file preview helpers.

Canonical implementation lives in `app.core.files.preview_temp`. This module
remains importable for legacy callers and test monkeypatch surfaces.
"""

from __future__ import annotations

import sys as _sys

from app.core.files import preview_temp as _canonical

_sys.modules[__name__] = _canonical
