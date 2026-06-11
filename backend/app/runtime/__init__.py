"""
Runtime composition boundary.

"""

from app.runtime.services import RuntimeServices, build_runtime_services
from app.runtime.router_registry import (
    ARRICCHISCI_BINDING,
    CARICA_BINDING,
    CONNECTIONS_BINDING,
    DATASOURCES_BINDING,
    ESTRAI_BINDING,
    ORCHESTRATOR_BINDING,
    PROJECTS_BINDING,
    RouterBinding,
    bind_router_services,
)

__all__ = [
    "RuntimeServices",
    "build_runtime_services",
    "RouterBinding",
    "bind_router_services",
    "ORCHESTRATOR_BINDING",
    "CONNECTIONS_BINDING",
    "PROJECTS_BINDING",
    "ARRICCHISCI_BINDING",
    "DATASOURCES_BINDING",
    "CARICA_BINDING",
    "ESTRAI_BINDING",
]
