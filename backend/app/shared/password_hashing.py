"""Compatibility alias for the canonical password hashing boundary."""

from __future__ import annotations

import sys as _sys

from app.core.security import password_hashing as _canonical
from app.core.security.password_hashing import *  # noqa: F401,F403

_sys.modules[__name__] = _canonical
