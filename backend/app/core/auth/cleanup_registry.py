"""Registered cleanup handlers for auth group lifecycle deletion."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.core.arricchisci.cleanup import delete_api_connections_for_group
from app.core.auth.user_cleanup import delete_users_for_group
from app.core.connections.cleanup import delete_connections_for_group
from app.core.datasources.cleanup import delete_datasources_for_group
from app.core.files.cleanup import delete_file_sources_for_group
from app.core.projects.cleanup import (
    GROUP_PROJECT_CLEANUP_REPORT_KEYS,
    delete_projects_for_group_report,
)
from app.core.scheduler.cleanup import (
    delete_log_runs_for_group,
    delete_schedules_for_group,
)


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


def _delete_reported_domain_owned(
    name: str,
    report_keys: tuple[str, ...],
    cleanup: Callable[[str, CleanupLogger], Mapping[str, int]],
) -> GroupCleanupHandler:
    def _cleanup(
        group_id: str,
        report: MutableMapping[str, int],
        log: CleanupLogger,
    ) -> None:
        try:
            cleanup_report = cleanup(group_id, log)
        except Exception as exc:
            log.warning(f"Cascade delete {name} fallito: {exc}")
            return

        for report_key in report_keys:
            report[report_key] = cleanup_report.get(report_key, 0)

    return GroupCleanupHandler(
        name=name,
        report_keys=report_keys,
        cleanup=_cleanup,
    )


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
    _delete_domain_owned(
        "delete_file_sources",
        "file_sources",
        delete_file_sources_for_group,
    ),
    _delete_domain_owned(
        "delete_connections",
        "connections",
        delete_connections_for_group,
    ),
    _delete_reported_domain_owned(
        name="delete_projects",
        report_keys=GROUP_PROJECT_CLEANUP_REPORT_KEYS,
        cleanup=delete_projects_for_group_report,
    ),
    _delete_domain_owned("delete_users", "users", delete_users_for_group),
    _delete_domain_owned("delete_log_runs", "log_runs", delete_log_runs_for_group),
)


def delete_intermediate_runs_for_group_project(project_id: str) -> int:
    """Backward-compatible export for older auth cleanup imports."""
    from app.core.projects.cleanup import (
        delete_intermediate_runs_for_group_project as delete_project_runs,
    )

    return delete_project_runs(project_id)


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
