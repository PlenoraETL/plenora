"""KEEP_COMPAT wrapper for the canonical files service boundary.

The implementation lives in ``app.core.files.service``. This module remains
for legacy imports and monkeypatch surfaces while production call-sites migrate
to the core-owned boundary.
"""

from __future__ import annotations

import sys as _sys

from app.core.files import service as _canonical

_sys.modules[__name__] = _canonical
