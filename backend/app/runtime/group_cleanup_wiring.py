"""Wiring composition-root dei cleanup handler di gruppo (Epic E).

Qui — e solo qui — i cleanup di dominio vengono importati e composti nel
registry neutro `app.core.lifecycle.group_cleanup`. Aggiungere un nuovo
dominio al cascade delete significa toccare QUESTO file, non `auth`.

L'ordine degli handler e' un contratto (stesso ordine dello storico
`auth.cleanup_registry`): schedules, api_connections, datasources,
file_sources, connections, projects (report multiplo), users, log_runs.
"""

from __future__ import annotations

from app.core.arricchisci.cleanup import delete_api_connections_for_group
from app.core.auth.user_cleanup import delete_users_for_group
from app.core.connections.cleanup import delete_connections_for_group
from app.core.datasources.cleanup import delete_datasources_for_group
from app.core.files.cleanup import delete_file_sources_for_group
from app.core.lifecycle.group_cleanup import (
    GroupCleanupHandler,
    domain_owned_handler,
    register_group_cleanup_handlers,
    reported_domain_owned_handler,
)
from app.core.projects.cleanup import (
    GROUP_PROJECT_CLEANUP_REPORT_KEYS,
    delete_projects_for_group_report,
)
from app.core.scheduler.cleanup import (
    delete_log_runs_for_group,
    delete_schedules_for_group,
)


def default_group_cleanup_handlers() -> tuple[GroupCleanupHandler, ...]:
    """Composizione canonica degli handler domain-owned, in ordine."""
    return (
        domain_owned_handler(
            "delete_schedules",
            "schedules",
            delete_schedules_for_group,
        ),
        domain_owned_handler(
            "delete_api_connections",
            "api_connections",
            delete_api_connections_for_group,
        ),
        domain_owned_handler(
            "delete_datasources",
            "datasources",
            delete_datasources_for_group,
        ),
        domain_owned_handler(
            "delete_file_sources",
            "file_sources",
            delete_file_sources_for_group,
        ),
        domain_owned_handler(
            "delete_connections",
            "connections",
            delete_connections_for_group,
        ),
        reported_domain_owned_handler(
            name="delete_projects",
            report_keys=GROUP_PROJECT_CLEANUP_REPORT_KEYS,
            cleanup=delete_projects_for_group_report,
        ),
        domain_owned_handler("delete_users", "users", delete_users_for_group),
        domain_owned_handler(
            "delete_log_runs", "log_runs", delete_log_runs_for_group
        ),
    )


def register_default_group_cleanup_handlers() -> None:
    """Registra i default nel registry lifecycle. Idempotente."""
    register_group_cleanup_handlers(default_group_cleanup_handlers())


__all__ = [
    "default_group_cleanup_handlers",
    "register_default_group_cleanup_handlers",
]
