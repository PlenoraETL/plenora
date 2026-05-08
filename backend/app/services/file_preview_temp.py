"""KEEP_COMPAT wrapper for temporary file preview helpers.

Canonical implementation lives in `app.core.files.preview_temp`. This module
remains importable for legacy callers and test monkeypatch surfaces.
"""

from __future__ import annotations

from app.core.files.preview_temp import (
    _logger_module,
    _rmtree_safe,
    preview_file_temp,
)

__all__ = ["_logger_module", "_rmtree_safe", "preview_file_temp"]
