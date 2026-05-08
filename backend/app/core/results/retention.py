"""Result retention workflow owned by the results domain."""

from __future__ import annotations

from app.utils.logger import get_logger


logger = get_logger(__name__)

DEFAULT_RESULTS_RETENTION = 10


def apply_results_retention(project_id: str) -> None:
    """
    Apply the configured retention policy for one project's results.

    Retention failures are intentionally non-fatal so a successful result save
    is not rolled back by cleanup of older rows.
    """
    try:
        from app.core.projects.runtime_context import resolve_project_runtime_retention_n
        from app.core.results import cleanup as results_cleanup
        from app.core.results import store as results_store

        retention_n = resolve_project_runtime_retention_n(
            project_id,
            default=DEFAULT_RESULTS_RETENTION,
        )

        all_results = results_store.list_result_rows(project_id)
        if len(all_results) <= retention_n:
            return

        to_delete = all_results[retention_n:]
        for row in to_delete:
            result_id = row.id
            results_cleanup.delete_result(result_id)
            logger.debug(
                f"Retention: eliminato risultato {result_id} del progetto {project_id}",
                op="retention",
            )

        logger.info(
            f"Retention applicata su {project_id}: conservati {retention_n}, "
            f"eliminati {len(to_delete)}",
            op="retention",
        )

    except Exception as exc:
        logger.warning(
            f"Errore retention risultati ({project_id}): {exc}",
            op="retention",
        )


__all__ = ["DEFAULT_RESULTS_RETENTION", "apply_results_retention"]
