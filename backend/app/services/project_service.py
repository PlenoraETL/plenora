"""KEEP_COMPAT wrapper for the project domain service.

Canonical implementation lives in `app.core.projects.service`.
"""

from __future__ import annotations

import sys as _sys

from app.core.projects import service as _canonical

_sys.modules[__name__] = _canonical
