"""Application-level auth group cleanup boundary."""

from __future__ import annotations

from app.core.auth import db as auth_db
from app.core.intermediate.cleanup import delete_runs_for_canvas
from app.core.results.cleanup import delete_results_for_project
from app.db.pg_connection import execute, fetchall
from app.utils.logger import get_logger


_GROUP_SCOPED_DELETE_SQL: tuple[tuple[str, str], ...] = (
    ("schedules", "DELETE FROM schedules WHERE group_id = ?"),
    ("api_connections", "DELETE FROM api_connections WHERE group_id = ?"),
    ("datasources", "DELETE FROM datasources WHERE group_id = ?"),
    ("file_sources", "DELETE FROM file_sources WHERE group_id = ?"),
    ("connections", "DELETE FROM connections WHERE group_id = ?"),
)


def delete_intermediate_runs_for_group_project(project_id: str) -> int:
    """Delete intermediate runs for a project during group lifecycle cleanup."""
    return delete_runs_for_canvas(project_id)


def delete_group_cascade(group_id: str) -> dict[str, int]:
    """
    Cascade delete a group and related domain/config data.

    Auth persistence stays in ``auth.db``; this application boundary owns the
    cross-domain sequencing and preserves the existing best-effort cleanup
    policy for non-final steps.
    """
    log = get_logger("auth.group_cleanup.delete_group_cascade")
    report = {
        "schedules": 0,
        "api_connections": 0,
        "datasources": 0,
        "file_sources": 0,
        "connections": 0,
        "projects": 0,
        "users": 0,
        "results": 0,
        "intermediate_runs": 0,
        "log_runs": 0,
    }

    for report_key, sql in _GROUP_SCOPED_DELETE_SQL:
        try:
            cur = execute(sql, (group_id,))
            report[report_key] = cur.rowcount
        except Exception as exc:
            log.warning(f"Cascade delete {report_key} fallito: {exc}")

    try:
        projects = fetchall("SELECT id FROM projects WHERE group_id = ?", (group_id,))
        project_ids = [str(project["id"]) for project in projects]

        for project_id in project_ids:
            try:
                report["results"] += delete_results_for_project(project_id)
            except Exception as exc:
                log.warning(f"Cascade delete results progetto {project_id}: {exc}")

            try:
                report["intermediate_runs"] += (
                    delete_intermediate_runs_for_group_project(project_id)
                )
            except Exception as exc:
                log.warning(
                    f"Cascade delete intermediate progetto {project_id}: {exc}"
                )

        if project_ids:
            try:
                execute(
                    "DELETE FROM comparison_keys WHERE canvas_id IN "
                    "(SELECT id FROM projects WHERE group_id = ?)",
                    (group_id,),
                )
            except Exception as exc:
                log.warning(f"Cascade batch delete comparison_keys fallito: {exc}")

            try:
                cur = execute("DELETE FROM projects WHERE group_id = ?", (group_id,))
                report["projects"] = (
                    cur.rowcount if hasattr(cur, "rowcount") else len(project_ids)
                )
            except Exception as exc:
                log.warning(f"Cascade batch delete projects fallito: {exc}")
    except Exception as exc:
        log.warning(f"Cascade delete projects fallito: {exc}")

    try:
        cur = execute("DELETE FROM users WHERE group_id = ?", (group_id,))
        report["users"] = cur.rowcount
    except Exception as exc:
        log.warning(f"Cascade delete users fallito: {exc}")

    try:
        cur = execute("DELETE FROM log_runs WHERE group_id = ?", (group_id,))
        report["log_runs"] = cur.rowcount
    except Exception as exc:
        log.warning(f"Cascade delete log_runs fallito: {exc}")

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
