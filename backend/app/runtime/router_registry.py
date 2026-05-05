"""Typed router-init binding registry - first slice.

BE-GO-READY-BOOTSTRAP-ENGINE-018-ROUTER-REGISTRY-FIRST-SLICE.

Goal
====
Reduce the procedural-fragility surface of the `init_*(<service>, ...)`
calls in ``app/main.py`` (lines 433-438 prior to this stream) by
introducing a *typed* binding helper that maps a router's init function
to the specific :class:`RuntimeServices` field(s) it consumes, then
dispatches the init exactly once with the container-owned instance.

Why this is NOT a service locator
---------------------------------
A service locator is a **runtime** lookup pattern where business code
asks "give me service X" at call-time. That pattern is explicitly out
of scope for the bootstrap engine work (BE_RULES: scope narrow, plus
the audit roadmap rules out service-locator-from-business-code).

This module is **build-time wiring only**: ``bind_router_services()``
is invoked exactly once during application startup, immediately after
``build_runtime_services()`` has constructed the typed container. The
routers themselves keep their existing module-private singleton
(``_orchestrator``, ``_project_service``, ...) - no router queries the
registry at request-time.

Why a registry instead of just renaming
---------------------------------------
The previous form

::

    init_orchestrator(orchestrator)

was fragile because:

  1. The argument was a module-global rebound from the runtime
     container - drift between the container and the global was
     silent.
  2. The argument was *positional*: a refactor renaming a field on
     :class:`RuntimeServices` would not surface as a missing-kwarg
     error here, only later when the orchestrator misbehaved.
  3. There was no programmatic way for a contract test to assert
     "the router received the container-owned instance, not a
     freshly-constructed double".

The registry binding form

::

    bind_router_services(_runtime_services, ORCHESTRATOR_BINDING)

addresses all three:

  1. The binding extracts the field via a typed lambda
     (``lambda s: (s.orchestrator,)``); rebinding the module global
     is no longer the source of truth.
  2. A field rename triggers an ``AttributeError`` at the binding's
     extraction call, on first import - fail-fast.
  3. Contract tests can verify identity: the value passed to the
     init function ``is`` the container's field.

First slice
-----------
This stream wires **only one** binding: ``ORCHESTRATOR_BINDING``. The
other 5 ``init_*`` calls in ``main.py`` stay on the procedural form,
to be migrated by follow-up streams. The migration order is
intentionally narrow: this slice proves the pattern; broader uptake
is gated on a separate audit checkpoint (BE_BOOTSTRAP_ENGINE_AUDIT.md).

Bootstrap relationship
----------------------
The registry MUST be invoked AFTER ``app.bootstrap.bootstrap()`` and
``build_runtime_services()`` have completed successfully. The module
itself is import-safe (no DB / Redis / object storage at import).

Out of scope (per task brief)
-----------------------------
- DI container framework. The binding is a hand-wired typed value.
- Generic service-locator pattern from business code.
- Migrating the other 5 ``init_*`` calls.
- Replacing FastAPI's own ``include_router`` flow.
- Module-level globals in ``main.py`` (KEEP_COMPAT preserved).
"""

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


# ---------------------------------------------------------------------------
# Concrete bindings
# ---------------------------------------------------------------------------
#
# IMPORTANT: bindings live here (not in ``main.py``) so that contract
# tests can assert their typed shape without importing ``main.py``
# (which has heavy module-load side effects). The router's
# ``init_*`` is imported lazily inside the extract closure to keep
# this module cheap to load.


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

    BE-GO-READY-BOOTSTRAP-ENGINE-019: 2-arg variant. The connections
    router takes (connection_service, database_inspector). Both
    instances are owned by ``RuntimeServices`` and exposed by identity
    via the typed extract. The thunk pattern is identical to
    :func:`_orchestrator_init` (lazy import to keep registry module
    import-cheap).
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

    BE-GO-READY-BOOTSTRAP-ENGINE-020: 1-arg slice consuming
    ``services.project_service``. Same lazy-thunk pattern as the
    orchestrator binding (keeps the registry module import-cheap).
    """
    from app.core.projects.router import init_projects

    init_projects(*args, **kwargs)


PROJECTS_BINDING = RouterBinding(
    name="projects",
    init_fn=_projects_init,
    extract=lambda services: (services.project_service,),
)
