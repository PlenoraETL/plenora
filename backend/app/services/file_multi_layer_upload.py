"""KEEP_COMPAT wrapper for multi-layer/multi-sheet upload helpers.

Canonical implementation lives in `app.core.files.multi_layer_upload`.
This module remains importable for legacy callers and test patch surfaces.
"""

from __future__ import annotations

import sys as _sys

from app.core.files import multi_layer_upload as _canonical

_sys.modules[__name__] = _canonical
