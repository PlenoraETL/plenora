"""Compatibility alias for the object-storage infrastructure boundary.

Runtime code must import from ``app.infrastructure.storage.cloud_backends``.
This module remains only for older tests and external callers that patch or
import the historical shared path.
"""

from __future__ import annotations

import sys

from app.infrastructure.storage import cloud_backends as _cloud_backends

sys.modules[__name__] = _cloud_backends
