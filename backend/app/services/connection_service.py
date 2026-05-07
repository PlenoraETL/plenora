"""KEEP_COMPAT: legacy import path for the connections domain service.

Canonical module: ``app.core.connections.service``.
"""

from __future__ import annotations

import sys

from app.core.connections import service as _canonical_service

sys.modules[__name__] = _canonical_service
