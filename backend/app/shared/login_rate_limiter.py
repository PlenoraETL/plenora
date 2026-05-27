"""Compatibility alias for the canonical login rate limiter boundary."""

from __future__ import annotations

import sys as _sys

from app.core.security import login_rate_limiter as _canonical
from app.core.security.login_rate_limiter import *  # noqa: F401,F403

_sys.modules[__name__] = _canonical
