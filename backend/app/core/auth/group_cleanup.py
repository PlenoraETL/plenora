"""Application-level auth group cleanup boundary."""

from __future__ import annotations

from app.core.auth import cleanup_registry
from app.core.auth import db as auth_db
from app.core.auth.cleanup_registry import delete_intermediate_runs_for_group_project
from app.utils.logger import get_logger


def delete_group_cascade(group_id: str) -> dict[str, int]:
    """
    Cascade delete a group and related domain/config data.

    Auth persistence stays in ``auth.db``; this application boundary owns the
    cross-domain sequencing and preserves the existing best-effort cleanup
    policy for non-final steps.
    """
    log = get_logger("auth.group_cleanup.delete_group_cascade")
    report = cleanup_registry.build_empty_group_cleanup_report()
    cleanup_registry.run_group_cleanup_handlers(group_id, report, log)

    try:
        auth_db.delete_group(group_id)
    except Exception as exc:
        log.error(f"Eliminazione gruppo {group_id} fallita: {exc}")
        raise

    log.info(
        f"Cascade delete gruppo {group_id} completato: {report}",
        op="delete_group_cascade",
    )
    return report


__all__ = ["delete_group_cascade", "delete_intermediate_runs_for_group_project"]
