"""
Shared typed execution context contract.

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


__all__ = ["CanvasExecutionContext"]


@dataclass(frozen=True)
class CanvasExecutionContext:
    """Immutable per-execution canvas context.

    Attributes:
        project_id: executing project id. Always present.
        group_id: tenant binding. ``None`` when the project does not
            exist or the DB lookup failed - downstream consumers
            must fail-closed when ``group_id is None``.
        preview_limit: if ``int > 0``, truncates loaded DataFrames
            (preview UI). ``None`` or ``<= 0`` = full execution.
    """

    project_id: str
    group_id: Optional[str]
    preview_limit: Optional[int]
