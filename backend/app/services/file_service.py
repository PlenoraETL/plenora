"""KEEP_COMPAT wrapper for the canonical files service boundary.

The implementation lives in ``app.core.files.service``. This module remains
for legacy imports and monkeypatch surfaces while production call-sites migrate
to the core-owned boundary.
"""

from app.core.files.service import *  # noqa: F401,F403
from app.core.files.service import _logger, _rmtree_safe  # noqa: F401
