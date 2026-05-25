"""
Arricchisci execution context — typed boundary.

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import psycopg

from app.core.arricchisci.project_access import load_project_access_context
from app.core.arricchisci.exceptions import ExecutionContextError
from app.shared.execution.context import CanvasExecutionContext


__all__ = [
    "ArricchisciExecutionContext",
    "build_execution_context",
]


@dataclass(frozen=True)
class ArricchisciExecutionContext(CanvasExecutionContext):
    """Public arricchisci execution-context type.

    It intentionally preserves the domain-specific type name while
    inheriting the shared canvas execution-context shape. See
    `app.shared.execution.context.CanvasExecutionContext` for field
    documentation.
    """


def build_execution_context(
    project_id: str,
    preview_limit: Optional[int] = None,
) -> ArricchisciExecutionContext:
    """Costruisce un `ArricchisciExecutionContext` con il
    `group_id` letto dal DB.

    Fail-closed su errori `psycopg.Error`: re-raise come
    `ExecutionContextError` con `original_error` + `__cause__`
    impostati. Stesso contract del legacy
    `canvas_orchestrator.init_execution_context`.

    Su progetto inesistente (`project_ctx is None`):
    `group_id = None`, ramo dati legittimo. I mixin downstream
    fail-closed con messaggio "Contesto di sicurezza non
    determinabile (group_id mancante)".

    Pure function: nessun side effect su `svc` o stato globale.
    Post-OE012 il singleton non viene piu' mirror-ato (la
    funzione `apply_to_legacy_state` e' stata cancellata): i
    mixin consumano `ctx` direttamente.

    Args:
        project_id: id del progetto in esecuzione.
        preview_limit: opzionale `int>0` per truncating preview.

    Raises:
        ExecutionContextError: se il DB lookup fallisce con
            `psycopg.Error` (pool exhausted / connection lost /
            deadlock / query error).

    Returns:
        `ArricchisciExecutionContext` immutabile.
    """
    try:
        project_ctx = load_project_access_context(project_id)
    except psycopg.Error as e:
        raise ExecutionContextError(
            project_id,
            reason="errore lettura progetto dal DB",
            original_error=e,
        ) from e
    group_id = project_ctx.group_id if project_ctx else None
    return ArricchisciExecutionContext(
        project_id=project_id,
        group_id=group_id,
        preview_limit=preview_limit,
    )
