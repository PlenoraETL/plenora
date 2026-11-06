"""Beat sweep: pulizie complete in coda dei task ormai terminali.

UI conferma finale F8/G3 (06/11/2026). La pulizia completa di un task
(DELETE /tasks/{id}/full-cleanup) tocca solo un task terminale: se il task
non si ferma subito dopo l'annullamento, la pulizia resta in
``task_cleanup_requests`` e la fa questo sweep (ogni minuto, coda
control-plane), con la stessa funzione dell'endpoint
(``app.core.orchestrator.task_cleanup``).
"""

from __future__ import annotations

from app.core.orchestrator.task_cleanup import DeferredCleanupStats, run_deferred_cleanups


def run_deferred_task_cleanups() -> DeferredCleanupStats:
    """Corpo dell'actor: idempotente, le righe gia' fatte non ci sono piu'."""
    return run_deferred_cleanups()


__all__ = ["run_deferred_task_cleanups"]
