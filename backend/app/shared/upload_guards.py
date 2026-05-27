"""Compatibility alias for the canonical upload guard boundary."""

from __future__ import annotations

import sys as _sys

from app.core.security import upload_guards as _canonical
from app.core.security.upload_guards import *  # noqa: F401,F403

_sys.modules[__name__] = _canonical
