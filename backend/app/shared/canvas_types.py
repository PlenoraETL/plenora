"""Compatibility alias for execution canvas type contracts."""

from __future__ import annotations

import sys as _sys

from app.shared.execution import canvas_types as _canonical
from app.shared.execution.canvas_types import *  # noqa: F401,F403

_sys.modules[__name__] = _canonical
