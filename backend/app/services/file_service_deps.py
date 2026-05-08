"""KEEP_COMPAT wrapper for file service dependency protocols/defaults.

The canonical dependency boundary lives in ``app.core.files.service_deps``.
This module remains for legacy imports and monkeypatch surfaces while
production call-sites migrate to the core-owned boundary.
"""

from app.core.files.service_deps import *  # noqa: F401,F403
