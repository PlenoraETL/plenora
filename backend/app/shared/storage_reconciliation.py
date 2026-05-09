"""Compatibility shim for storage reconciliation.

The implementation lives in `app.core.resources.storage_reconciliation`.
Runtime code must import the domain boundary directly.
"""

from __future__ import annotations

from app.core.resources.storage_reconciliation import *  # noqa: F401,F403
from app.core.resources.storage_reconciliation import __all__  # noqa: F401
