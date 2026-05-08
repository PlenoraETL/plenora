"""KEEP_COMPAT wrapper for file-source blob storage dispatch.

Canonical implementation lives in `app.core.files.storage_dispatch`.
This module remains importable for legacy callers and test patch surfaces.
"""

from __future__ import annotations

import sys as _sys

from app.core.files import storage_dispatch as _canonical

_sys.modules[__name__] = _canonical
