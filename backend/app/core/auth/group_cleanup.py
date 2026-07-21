"""Application-level auth group cleanup boundary."""

from __future__ import annotations

from app.core.auth import cleanup_registry
from app.core.auth import db as auth_db
from app.core.auth.cleanup_registry import (
    GroupCleanupReport,
    delete_intermediate_runs_for_group_project,
)
from app.utils.logger import get_logger


class GroupCleanupIncompleteError(RuntimeError):
    """At least one domain cleanup failed; the group remains retryable."""


def delete_group_cascade(group_id: str) -> GroupCleanupReport:
    """
    Cascade delete a group and related domain/config data.

    Auth persistence stays in ``auth.db``; this application boundary owns the
    cross-domain sequencing. Domain failures are isolated long enough to run
    the remaining cleanup handlers, then fail closed before deleting auth.
    """
    log = get_logger("auth.group_cleanup.delete_group_cascade")
    report = cleanup_registry.build_empty_group_cleanup_report()
    failures = cleanup_registry.run_group_cleanup_handlers(group_id, report, log)
    if failures:
        failed_names = ", ".join(failures)
        log.error(
            "Cancellazione gruppo %s interrotta: cleanup incompleti (%s)",
            group_id,
            failed_names,
        )
        raise GroupCleanupIncompleteError(
            f"Cleanup gruppo incompleto: {failed_names}"
        )

    auth_db.delete_group(group_id)

    log.info(
        f"Cascade delete gruppo {group_id} completato: {report}",
        op="delete_group_cascade",
    )
    return report


__all__ = [
    "GroupCleanupIncompleteError",
    "delete_group_cascade",
    "delete_intermediate_runs_for_group_project",
]
