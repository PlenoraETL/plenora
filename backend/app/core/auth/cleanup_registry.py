"""Registered cleanup handlers for auth group lifecycle deletion."""

from __future__ import annotations

import time
from collections.abc import Callable, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.core.arricchisci.cleanup import delete_api_connections_for_group
from app.core.connections.cleanup import delete_connections_for_group
from app.core.datasources.cleanup import delete_datasources_for_group
from app.core.projects.cleanup import (
    delete_intermediate_runs_for_group_project,
    delete_projects_for_group,
)
from app.core.scheduler.cleanup import delete_schedules_for_group
from app.db.pg_connection import execute


GROUP_CLEANUP_REPORT_KEYS: tuple[str, ...] = (
    "schedules",
    "api_connections",
    "datasources",
    "file_sources",
    "connections",
    "projects",
    "users",
    "results",
    "intermediate_runs",
    "log_runs",
)


class CleanupLogger(Protocol):
    def info(self, msg: str, *args, **kwargs) -> None: ...

    def warning(self, msg: str, *args, **kwargs) -> None: ...


@dataclass(frozen=True)
class GroupCleanupHandler:
    """Named group cleanup unit registered with the auth lifecycle boundary."""

    name: str
    report_keys: tuple[str, ...]
    cleanup: Callable[[str, MutableMapping[str, int], CleanupLogger], None]

    def run(
        self,
        group_id: str,
        report: MutableMapping[str, int],
        log: CleanupLogger,
    ) -> None:
        self.cleanup(group_id, report, log)


def _rowcount(cursor, fallback: int = 0) -> int:
    return cursor.rowcount if hasattr(cursor, "rowcount") else fallback


def _delete_group_scoped(report_key: str, sql: str) -> GroupCleanupHandler:
    def _cleanup(
        group_id: str,
        report: MutableMapping[str, int],
        log: CleanupLogger,
    ) -> None:
        try:
            report[report_key] = _rowcount(execute(sql, (group_id,)))
        except Exception as exc:
            log.warning(f"Cascade delete {report_key} fallito: {exc}")

    return GroupCleanupHandler(
        name=f"delete_{report_key}",
        report_keys=(report_key,),
        cleanup=_cleanup,
    )


def _delete_domain_owned(
    name: str,
    report_key: str,
    cleanup: Callable[[str], int],
) -> GroupCleanupHandler:
    def _cleanup(
        group_id: str,
        report: MutableMapping[str, int],
        log: CleanupLogger,
    ) -> None:
        try:
            report[report_key] = cleanup(group_id)
        except Exception as exc:
            log.warning(f"Cascade delete {report_key} fallito: {exc}")

    return GroupCleanupHandler(
        name=name,
        report_keys=(report_key,),
        cleanup=_cleanup,
    )


def _cleanup_projects(
    group_id: str,
    report: MutableMapping[str, int],
    log: CleanupLogger,
) -> None:
    project_report = delete_projects_for_group(group_id, log)
    report["projects"] = project_report.projects
    report["results"] += project_report.results
    report["intermediate_runs"] += project_report.intermediate_runs


GROUP_CLEANUP_HANDLERS: tuple[GroupCleanupHandler, ...] = (
    _delete_domain_owned(
        "delete_schedules",
        "schedules",
        delete_schedules_for_group,
    ),
    _delete_domain_owned(
        "delete_api_connections",
        "api_connections",
        delete_api_connections_for_group,
    ),
    _delete_domain_owned(
        "delete_datasources",
        "datasources",
        delete_datasources_for_group,
    ),
    _delete_group_scoped(
        "file_sources",
        "DELETE FROM file_sources WHERE group_id = ?",
    ),
    _delete_domain_owned(
        "delete_connections",
        "connections",
        delete_connections_for_group,
    ),
    GroupCleanupHandler(
        name="delete_projects",
        report_keys=("projects", "results", "intermediate_runs"),
        cleanup=_cleanup_projects,
    ),
    _delete_group_scoped("users", "DELETE FROM users WHERE group_id = ?"),
    _delete_group_scoped("log_runs", "DELETE FROM log_runs WHERE group_id = ?"),
)


def build_empty_group_cleanup_report() -> dict[str, int]:
    return dict.fromkeys(GROUP_CLEANUP_REPORT_KEYS, 0)


def get_group_cleanup_handlers() -> Sequence[GroupCleanupHandler]:
    return GROUP_CLEANUP_HANDLERS


def run_group_cleanup_handlers(
    group_id: str,
    report: MutableMapping[str, int],
    log: CleanupLogger,
    handlers: Sequence[GroupCleanupHandler] | None = None,
) -> None:
    ordered_handlers = tuple(handlers or get_group_cleanup_handlers())
    total = len(ordered_handlers)

    for index, handler in enumerate(ordered_handlers, start=1):
        started_at = time.perf_counter()
        try:
            handler.run(group_id, report, log)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            log.warning(
                "Cascade cleanup handler %s failed after %.2fms "
                "(%s/%s): %s",
                handler.name,
                elapsed_ms,
                index,
                total,
                exc,
            )
            continue

        elapsed_ms = (time.perf_counter() - started_at) * 1000
        log.info(
            "Cascade cleanup handler %s completed in %.2fms (%s/%s)",
            handler.name,
            elapsed_ms,
            index,
            total,
        )


__all__ = [
    "GROUP_CLEANUP_HANDLERS",
    "GROUP_CLEANUP_REPORT_KEYS",
    "GroupCleanupHandler",
    "build_empty_group_cleanup_report",
    "delete_intermediate_runs_for_group_project",
    "get_group_cleanup_handlers",
    "run_group_cleanup_handlers",
]
