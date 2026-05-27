"""Compatibility alias for the canonical token blacklist boundary."""

from __future__ import annotations

import sys as _sys

from app.core.security import token_blacklist as _canonical
from app.core.security.token_blacklist import *  # noqa: F401,F403

_sys.modules[__name__] = _canonical
