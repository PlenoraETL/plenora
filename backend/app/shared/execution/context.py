"""Shared typed execution context contract.

BE-GO-READY-ARCH-056-W1-SHARED-CANVAS-EXECUTION-CONTEXT

Defines `CanvasExecutionContext`: the immutable, domain-agnostic
per-execution boundary type shared across canvas engines
(Arricchisci, and future Manipola migration).

Go-ready mapping
================
    type CanvasExecutionContext struct {
        ProjectID    string
        GroupID      *string  // nil = security context unknown (fail-closed downstream)
        PreviewLimit *int     // nil or <=0 = full execution
    }

Leaf contract
=============
No imports from `app.core.*`. No DB, no HTTP, no Pandas.
Domain engines import this module; this module never imports them.
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
