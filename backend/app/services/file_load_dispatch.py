"""Compatibility surface for file parser dispatch.

Canonical implementation: `app.core.files.load_dispatch`.
"""

from __future__ import annotations

import sys as _sys

from app.core.files import load_dispatch as _canonical

_sys.modules[__name__] = _canonical
