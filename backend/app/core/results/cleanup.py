"""Canonical lifecycle boundary for result cleanup operations.

The SQL implementation and storage reconciliation ordering remain in the
legacy DB leaf while this module becomes the domain-owned import target for
callers that orchestrate cleanup.
"""


def delete_result(result_id: str) -> bool:
    """Delete one result row and its owned storage through the SQL boundary."""
    from app.db import results_db_service

    return results_db_service.delete_result(result_id)


def delete_results_for_project(project_id: str) -> int:
    """Delete all result rows and owned storage for a project."""
    from app.db import results_db_service

    return results_db_service.delete_results_for_project(project_id)
