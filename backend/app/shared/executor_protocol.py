"""Canvas executor protocol — shared execution contract.

BE-GO-READY-ARCH-055-W2-EXECUTION-ENGINE-DUPLICATION-SEAM.

Defines the Protocol that all canvas pipeline executors implement
structurally. Currently satisfied (duck-typing, no inheritance needed)
by:

- ``EstraiExecutor``   — ``app.core.estrai.executor``
- ``ManipolaExecutor`` — ``app.core.manipola.executor``
- ``EnrichExecutor``   — ``app.core.arricchisci.executor``

All three are dispatched through ``OrchestratorService`` (orchestrator/
service.py) using the same ``execute`` / ``preview`` surface. This
Protocol makes the shared contract explicit, enables a typed dispatch
table in the orchestrator, and maps cleanly to a Go interface.

Follows ADR-0007: ``typing.Protocol`` + ``@runtime_checkable`` instead
of ``abc.ABC``. Concrete executors remain plain classes; zero inheritance
required.

Boundary
========
This is a leaf contract module. It MUST NOT import:

- ``fastapi`` / ``starlette`` (no HTTP / Depends);
- ``app.db.*`` / ``psycopg2`` / ``redis`` (no I/O);
- ``pandas`` / ``geopandas`` / ``polars`` (no DataFrame work);
- ``app.core.*`` (consumed by core, not vice-versa).

``app.models.CanvasState`` is allowed as the canonical canvas domain
model (imported under ``TYPE_CHECKING`` to avoid circular load-time
imports).

Go-ready mapping
================
::

    type CanvasExecutor interface {
        Execute(ctx context.Context, projectID string,
                canvas CanvasState, opts ExecuteOpts) (map[string]any, error)
        Preview(ctx context.Context, projectID string,
                canvas CanvasState, limit int) (map[string]any, error)
    }
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional, Protocol, runtime_checkable


if TYPE_CHECKING:
    from app.models import CanvasState


__all__ = ["CanvasExecutorProtocol"]


@runtime_checkable
class CanvasExecutorProtocol(Protocol):
    """Structural protocol for all canvas pipeline executors.

    Minimal shared surface: the two methods ``execute`` and ``preview``
    are defined with the common positional args. Engine-specific extras
    (``force_recalculate``, ``format``) are optional kwargs on the
    concrete impls; callers using this Protocol pass only the shared
    positional args.

    Usage — typed dispatch table in OrchestratorService::

        executors: Dict[str, CanvasExecutorProtocol] = {
            "estrai":      self.estrai_executor,
            "manipola":    self.manipola_executor,
            "arricchisci": self.enrich_executor,
        }
        result = executors[module_type].execute(project_id, canvas)

    Invariants:

    - ``execute()`` returns ``{"success": bool, ...}`` dict and never
      raises for domain failures (auth, validation, empty data). Only
      infrastructure errors (OOM, unhandled crash) propagate as
      exceptions.
    - ``preview()`` never persists output; always truncates to
      ``limit`` rows.
    - Both methods are synchronous (Celery worker context).
    - Return dict shape: ``success``, ``message`` / ``error`` keys
      for failures. Engines differ on ``message`` vs ``error`` key
      name — unification is a next-step item (see seam doc).
    """

    def execute(
        self,
        project_id: str,
        canvas: "CanvasState",
        run_id: Optional[str] = None,
        intermediate_mode_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run the full pipeline: load sources → process → persist result.

        Returns ``{"success": True, ...}`` on success or
        ``{"success": False, "message": str}`` on domain failure.

        Engine-specific kwargs (``force_recalculate``, ``format``) are
        present on concrete impls but not captured by this Protocol;
        orchestrator callers that use the Protocol type pass them via
        explicit dispatch or leave them at their defaults.
        """
        ...

    def preview(
        self,
        project_id: str,
        canvas: "CanvasState",
        limit: int,
    ) -> Dict[str, Any]:
        """Run the pipeline without persisting. Truncate output to ``limit`` rows.

        Returns ``{"success": True, "columns": [...], "rows": [...], ...}``
        or ``{"success": False, "message": str}`` on failure.
        """
        ...
