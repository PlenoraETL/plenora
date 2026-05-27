"""Compatibility alias for execution clock primitives."""

from __future__ import annotations

import sys as _sys

from app.shared.execution import clock as _canonical
from app.shared.execution.clock import *  # noqa: F401,F403

_sys.modules[__name__] = _canonical
