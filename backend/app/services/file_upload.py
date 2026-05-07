"""KEEP_COMPAT wrapper for single-file upload helpers.

Canonical implementation lives in `app.core.files.upload`. This module remains
importable for legacy callers and test monkeypatch surfaces.
"""

from __future__ import annotations

import sys as _sys

from app.core.files import upload as _canonical

_sys.modules[__name__] = _canonical
