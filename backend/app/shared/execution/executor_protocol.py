"""Canvas executor protocol - shared execution contract.

BE-GO-READY-ARCH-055-W2-EXECUTION-ENGINE-DUPLICATION-SEAM.

Defines the Protocol that all canvas pipeline executors implement
structurally. Currently satisfied (duck-typing, no inheritance needed)
by:

- ``EstraiExecutor``   - ``app.core.estrai.executor``
- ``ManipolaExecutor`` - ``app.core.manipola.executor``
- ``EnrichExecutor``   - ``app.core.arricchisci.executor``

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

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional, Protocol, runtime_checkable

from app.shared.execution.payloads import ArtifactPayload, _check_payload_safe

if TYPE_CHECKING:
    from app.models import CanvasState


# ArtifactPayload re-exported here so existing callers that import it from
# executor_protocol continue to work without changes.
__all__ = ["CanvasExecutorProtocol", "ExecutionResult", "ArtifactPayload"]


@dataclass(frozen=True)
class ExecutionResult:
    """Typed return value for canvas executor results.

    Normalizes the loose ``dict[str, Any]`` that executors return today.
    Surfaces ``success``, ``message``, and ``error`` as explicit typed
    fields; all other executor keys (``result_id``, ``output_path``,
    ``dataframe``, ``rows_count``, etc.) are preserved in ``payload`` for
    backward-compatible dict reconstruction.

    Normalization rule: both ``message`` (most executors) and ``error``
    (arricchisci-style failure keys) are captured explicitly; ``to_dict``
    reconstructs only the keys that are not None so the output is stable
    across engine differences.

    Boundary: leaf contract - no Pandas, DB, FastAPI, or ArcGIS imports.

    Go-ready mapping::

        type ExecutionResult struct {
            Success bool
            Message *string
            Error   *string
            Payload map[string]any
        }
    """

    success: bool
    message: Optional[str] = None
    error: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _check_payload_safe(self.payload)

    # -- factory -----------------------------------------------------------------

    @classmethod
    def from_executor_dict(cls, d: Any) -> "ExecutionResult":
        """Convert an executor's raw dict to a typed ``ExecutionResult``.

        Non-dict input produces a failed result (mirrors
        ``PipelineArtifact.from_executor_result`` semantics for non-dict).
        """
        if not isinstance(d, dict):
            return cls(success=False, message="result non strutturato")
        return cls(
            success=bool(d.get("success", False)),
            message=d.get("message"),
            error=d.get("error"),
            payload={k: v for k, v in d.items() if k not in ("success", "message", "error")},
        )

    # -- converter ---------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Reconstruct a backward-compatible dict for downstream consumers.

        ``PipelineArtifact.from_executor_result`` and legacy callers that
        read ``result["success"]`` all receive the same shape they would
        from the raw executor dict.
        """
        out: Dict[str, Any] = {"success": self.success}
        if self.message is not None:
            out["message"] = self.message
        if self.error is not None:
            out["error"] = self.error
        out.update(self.payload)
        return out

    def artifact_payload(self) -> Optional[ArtifactPayload]:
        """Extract typed ``ArtifactPayload`` from this result's payload.

        Returns ``None`` for failure results - no artifact was produced.
        For success results, extracts the standard artifact reference keys
        (``result_id``, ``output_path``, ``filename``, ``format``,
        ``rows_count``, ``run_id``) from ``self.payload``.

        Extra payload keys not covered by ``ArtifactPayload`` (e.g.
        ``user_error``, ``error_node_id``) remain in ``self.payload``
        and are not surfaced by this accessor; use ``self.payload`` or
        ``to_dict()`` to access them.
        """
        if not self.success:
            return None
        return ArtifactPayload.from_payload_dict(self.payload)


@runtime_checkable
class CanvasExecutorProtocol(Protocol):
    """Structural protocol for all canvas pipeline executors.

    Minimal shared surface: the two methods ``execute`` and ``preview``
    are defined with the common positional args. Engine-specific extras
    (``force_recalculate``, ``format``) are optional kwargs on the
    concrete impls; callers using this Protocol pass only the shared
    positional args.

    Usage - typed dispatch table in OrchestratorService::

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
      name; ``ExecutionResult.from_executor_dict`` normalises both.
    - Target return type is ``ExecutionResult``; current impls still
      return ``Dict[str, Any]`` - wrap at the orchestrator boundary.
    """

    def execute(
        self,
        project_id: str,
        canvas: "CanvasState",
        run_id: Optional[str] = None,
        intermediate_mode_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run the full pipeline: load sources -> process -> persist result.

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
