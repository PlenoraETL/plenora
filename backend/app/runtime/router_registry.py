"""Typed router-init binding registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Tuple

if TYPE_CHECKING:
    from app.runtime.services import RuntimeServices


__all__ = [
    "RouterBinding",
    "bind_router_services",
    "ORCHESTRATOR_BINDING",
    "CONNECTIONS_BINDING",
    "PROJECTS_BINDING",
    "ARRICCHISCI_BINDING",
    "DATASOURCES_BINDING",
    "CARICA_BINDING",
    "CARICA_DISPATCH_BINDING",
    "ESTRAI_BINDING",
]


@dataclass(frozen=True)
class RouterBinding:
    """A single (router init function, services accessor) pair.

    Fields:
      name: human-readable label for the binding. Used in audit logs
        and test assertions; must be stable across releases (a rename
        is a contract change).
      init_fn: the router's ``init_*`` function. Called exactly once
        with the positional args returned by ``extract``.
      extract: pure mapping from the typed container to the positional
        arg tuple consumed by ``init_fn``. Lambdas are fine: this is
        wiring, not business logic.

    The dataclass is frozen so accidental mutation surfaces as an
    error rather than a silent rebind.
    """

    name: str
    init_fn: Callable[..., None]
    extract: Callable[["RuntimeServices"], Tuple[Any, ...]]


def bind_router_services(
    services: "RuntimeServices",
    binding: RouterBinding,
) -> None:
    """Invoke ``binding.init_fn`` with the container-owned args.

    Contract:
      - ``binding.extract(services)`` returns a tuple. The tuple is
        splatted into ``init_fn`` positionally. This matches the
        existing ``init_*`` signatures exactly - no opaque dict, no
        kwargs surprise.
      - The function returns ``None``. Routers' ``init_*`` set their
        module-private singleton; the registry intentionally does not
        capture the result.
      - On ``AttributeError`` from ``extract`` (typo on the field name)
        or ``TypeError`` from ``init_fn`` (signature drift) the
        exception propagates to the caller. Fail-fast on the startup
        path is preferable to a silent runtime hazard.
    """
    args = binding.extract(services)
    binding.init_fn(*args)


# NOTE: bindings live here (not in ``main.py``) so that contract
# tests can assert their typed shape without importing ``main.py``
# which has heavy module-load side effects). The router's
# ``init_*`` is imported lazily inside the extract closure to keep
# this module cheap to load


def _orchestrator_init(*args: Any, **kwargs: Any) -> None:
    """Lazy thunk: import + invoke ``init_orchestrator``.

    The router module is imported at call-time (not import-time) so
    that ``app.runtime.router_registry`` itself can be imported by
    tests without dragging in the full FastAPI router module graph.
    """
    from app.core.orchestrator.router import init_orchestrator

    init_orchestrator(*args, **kwargs)


ORCHESTRATOR_BINDING = RouterBinding(
    name="orchestrator",
    init_fn=_orchestrator_init,
    extract=lambda services: (services.orchestrator,),
)


def _connections_init(*args: Any, **kwargs: Any) -> None:
    """Lazy thunk: import + invoke ``init_connections``.

    The connections router takes (connection_service, database_inspector).
    Both instances are owned by ``RuntimeServices`` and exposed by identity
    via the typed extract.
    """
    from app.core.connections.router import init_connections

    init_connections(*args, **kwargs)


CONNECTIONS_BINDING = RouterBinding(
    name="connections",
    init_fn=_connections_init,
    extract=lambda services: (
        services.connection_service,
        services.database_inspector,
    ),
)


def _projects_init(*args: Any, **kwargs: Any) -> None:
    """Lazy thunk: import + invoke ``init_projects``.

    Consumes ``services.project_service``.
    """
    from app.core.projects.router import init_projects

    init_projects(*args, **kwargs)


PROJECTS_BINDING = RouterBinding(
    name="projects",
    init_fn=_projects_init,
    extract=lambda services: (services.project_service,),
)


def _arricchisci_init(*args: Any, **kwargs: Any) -> None:
    """Lazy thunk: import + invoke ``init_arricchisci``.

    Consumes ``services.project_service`` and the canonical
    ``services.api_connection_service`` singleton.
    """
    from app.core.arricchisci.router import init_arricchisci

    init_arricchisci(*args, **kwargs)


ARRICCHISCI_BINDING = RouterBinding(
    name="arricchisci",
    init_fn=_arricchisci_init,
    extract=lambda services: (
        services.project_service,
        services.api_connection_service,
    ),
)


def _datasources_init(*args: Any, **kwargs: Any) -> None:
    """Lazy thunk: import + invoke ``init_datasources``.

    Consumes the canonical ``services.datasource_service`` singleton.
    """
    from app.core.datasources.router import init_datasources

    init_datasources(*args, **kwargs)


DATASOURCES_BINDING = RouterBinding(
    name="datasources",
    init_fn=_datasources_init,
    extract=lambda services: (services.datasource_service,),
)


def _carica_init(*args: Any, **kwargs: Any) -> None:
    """Lazy thunk: import + invoke ``init_carica_service``.

    Preserves the router's positional ``init_carica_service(
    connection_service)`` contract while routing through the typed registry.
    """
    from app.core.carica.router import init_carica_service

    init_carica_service(*args, **kwargs)


CARICA_BINDING = RouterBinding(
    name="carica",
    init_fn=_carica_init,
    extract=lambda services: (services.connection_service,),
)


def _carica_dispatch_init(*args: Any, **kwargs: Any) -> None:
    """Lazy thunk: import + invoke ``init_carica_dispatch``.

    Il dispatch CARICA (sink db/datasource/rest/arcgis) consuma
    project/connection/datasource service dal composition root invece di
    costruirli inline.
    """
    from app.core.carica.dispatch import init_carica_dispatch

    init_carica_dispatch(*args, **kwargs)


CARICA_DISPATCH_BINDING = RouterBinding(
    name="carica_dispatch",
    init_fn=_carica_dispatch_init,
    extract=lambda services: (
        services.project_service,
        services.connection_service,
        services.datasource_service,
    ),
)


def _estrai_init(*args: Any, **kwargs: Any) -> None:
    """Lazy thunk: import + invoke ``init_estrai``.

    Il preview formula di EstrAi consuma project/connection/query executor
    iniettati invece di costruirli inline.
    """
    from app.core.estrai.router import init_estrai

    init_estrai(*args, **kwargs)


ESTRAI_BINDING = RouterBinding(
    name="estrai",
    init_fn=_estrai_init,
    extract=lambda services: (
        services.project_service,
        services.connection_service,
        services.query_executor,
    ),
)
