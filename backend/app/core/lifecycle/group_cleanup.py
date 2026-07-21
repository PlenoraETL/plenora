"""Registry neutro dei cleanup handler per la cancellazione gruppo.

Il meccanismo (handler tipizzati, report, esecuzione ordinata con failure
isolation) vive qui, fuori da `auth`. I domini espongono le proprie funzioni
di cleanup; la registrazione dei default avviene al composition root
(`app/runtime/group_cleanup_wiring.py`); `auth.cleanup_registry` resta
come wrapper compatibile.

Questo modulo non importa alcun dominio `app.core`: foundation pura.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeAlias


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

GroupCleanupReport: TypeAlias = dict[str, int]


class CleanupLogger(Protocol):
    def info(self, msg: str, *args, **kwargs) -> None: ...

    def warning(self, msg: str, *args, **kwargs) -> None: ...


@dataclass(frozen=True)
class GroupCleanupHandler:
    """Named group cleanup unit registered with the lifecycle boundary."""

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


def domain_owned_handler(
    name: str,
    report_key: str,
    cleanup: Callable[[str], int],
) -> GroupCleanupHandler:
    """Handler per un cleanup di dominio `group_id -> count`."""

    def _cleanup(
        group_id: str,
        report: MutableMapping[str, int],
        log: CleanupLogger,
    ) -> None:
        report[report_key] = cleanup(group_id)

    return GroupCleanupHandler(
        name=name,
        report_keys=(report_key,),
        cleanup=_cleanup,
    )


def reported_domain_owned_handler(
    name: str,
    report_keys: tuple[str, ...],
    cleanup: Callable[[str, CleanupLogger], Mapping[str, int]],
) -> GroupCleanupHandler:
    """Handler per un cleanup di dominio che produce un report multiplo."""

    def _cleanup(
        group_id: str,
        report: MutableMapping[str, int],
        log: CleanupLogger,
    ) -> None:
        cleanup_report = cleanup(group_id, log)

        for report_key in report_keys:
            report[report_key] = cleanup_report.get(report_key, 0)

    return GroupCleanupHandler(
        name=name,
        report_keys=report_keys,
        cleanup=_cleanup,
    )


# Registry: popolato dal composition root (idempotente per nome).
_registered_handlers: list[GroupCleanupHandler] = []


def register_group_cleanup_handlers(
    handlers: Sequence[GroupCleanupHandler],
) -> None:
    """Registra handler in ordine; nomi gia' presenti vengono saltati
    (idempotenza: il wiring puo' essere invocato piu' volte)."""
    known = {handler.name for handler in _registered_handlers}
    for handler in handlers:
        if handler.name in known:
            continue
        _registered_handlers.append(handler)
        known.add(handler.name)


def get_group_cleanup_handlers() -> tuple[GroupCleanupHandler, ...]:
    return tuple(_registered_handlers)


def build_empty_group_cleanup_report() -> GroupCleanupReport:
    return dict.fromkeys(GROUP_CLEANUP_REPORT_KEYS, 0)


def run_group_cleanup_handlers(
    group_id: str,
    report: MutableMapping[str, int],
    log: CleanupLogger,
    handlers: Sequence[GroupCleanupHandler] | None = None,
) -> tuple[str, ...]:
    """Run every handler and return the names of failed cleanup units."""
    ordered_handlers = tuple(handlers or get_group_cleanup_handlers())
    total = len(ordered_handlers)
    failed_handlers: list[str] = []

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
            failed_handlers.append(handler.name)
            continue

        elapsed_ms = (time.perf_counter() - started_at) * 1000
        log.info(
            "Cascade cleanup handler %s completed in %.2fms (%s/%s)",
            handler.name,
            elapsed_ms,
            index,
            total,
        )
    return tuple(failed_handlers)


__all__ = [
    "GROUP_CLEANUP_REPORT_KEYS",
    "CleanupLogger",
    "GroupCleanupHandler",
    "GroupCleanupReport",
    "build_empty_group_cleanup_report",
    "domain_owned_handler",
    "get_group_cleanup_handlers",
    "register_group_cleanup_handlers",
    "reported_domain_owned_handler",
    "run_group_cleanup_handlers",
]
