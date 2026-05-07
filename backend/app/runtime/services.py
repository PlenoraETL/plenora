"""
Typed runtime composition factory — first slice.

BE-GO-READY-BOOTSTRAP-ENGINE-011-RUNTIME-CONTAINER-FIRST-SLICE.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.arricchisci.api_connector import ApiConnectionService
    from app.core.datasources.service import DataSourceService
    from app.core.estrai.database_inspector import DatabaseInspector
    from app.core.estrai.query_generator import QueryGenerator
    from app.core.features.features_service import FeaturesService
    from app.core.orchestrator.service import OrchestratorService
    from app.db.query_executor import QueryExecutor
    from app.services.connection_service import ConnectionService
    from app.services.project_service import ProjectService


__all__ = ["RuntimeServices", "build_runtime_services"]


@dataclass(frozen=True)
class RuntimeServices:
    """Frozen typed container for shared service singletons.

    Bundles the coherent slice previously constructed at module-load
    time in `app/main.py`. Each field is a constructed instance, NOT
    a class. Frozen so reassignments surface as errors instead of
    silently rebinding.

    Field roles:
      - `project_service`: project CRUD facade.
      - `connection_service`: external DB connection registry.
      - `database_inspector`: introspection over DB connections.
      - `query_generator`: ESTRAI SQL generation pipeline.
      - `query_executor`: raw query execution + result staging.
      - `features_service`: ArcGIS Feature Service consumer.
      - `orchestrator`: pipeline orchestrator (depends on the above 5,
        excluding `database_inspector`).
      - `api_connection_service`: arricchisci API connection
        singleton (added by BE-GO-READY-BOOTSTRAP-ENGINE-016).
        IMPORTANT: this field is the SAME object as the canonical
        module-level singleton
        `app.core.arricchisci.api_connector.api_connection_service`.
        The container does not own its construction — `api_connector`
        constructs it eagerly at module import as before. The
        container exposes it for explicit dependency wiring; the
        public import path + the test monkeypatch surface
        (`patch("app.core.arricchisci.api_connector.api_connection_service.<method>", ...)`)
        are preserved. Identity invariant locked by contract test.
      - `datasource_service`: datasources singleton (added by
        BE-GO-READY-BOOTSTRAP-ENGINE-017). Same "wrap, don't move"
        strategy as `api_connection_service`: identity-preserving
        re-export of the canonical
        `app.core.datasources.service.datasource_service` singleton.
        Construction stays at the module level
        (`service.py:440`); the container exposes the SAME object.
        `init_datasources(service)` consumes the container's field
        in `main.py`, no router-side change required.
    """

    project_service: "ProjectService"
    connection_service: "ConnectionService"
    database_inspector: "DatabaseInspector"
    query_generator: "QueryGenerator"
    query_executor: "QueryExecutor"
    features_service: "FeaturesService"
    orchestrator: "OrchestratorService"
    api_connection_service: "ApiConnectionService"
    datasource_service: "DataSourceService"


def build_runtime_services(
    *,
    settings: Any,
    save_function: Any,
) -> RuntimeServices:
    """Construct the first-slice shared services in dependency order.

    Args:
        settings: app `Settings` singleton. Read for
            `DATA_DIR` (composed into the executor's results dir).
        save_function: dataframe save function injected into
            `QueryExecutor` and `FeaturesService`. Today this is
            `app.utils.dataframe.save_dataframe`; injected to keep
            the factory dependency-explicit and to allow tests to
            substitute a stub.

    Returns:
        Fully wired `RuntimeServices`. The orchestrator references
        the 5 prior services by name (kw-only on
        `OrchestratorService.__init__`), so reordering the dependent
        construction would surface as a missing-kwarg `TypeError`
        rather than a silent runtime hazard.

    Raises:
        Whatever the underlying constructors raise. The factory does
        not wrap or remap; bootstrap-completion semantics are owned
        by `app.bootstrap`. A contract test locks the order
        invariant.
    """
    # Lazy imports keep this module cheap to load (e.g. for tests
    # that import only the typed dataclass to assert shape).
    from app.core.estrai.database_inspector import DatabaseInspector
    from app.core.estrai.query_generator import QueryGenerator
    from app.core.features.features_service import FeaturesService
    from app.core.orchestrator.service import OrchestratorService
    from app.db.query_executor import QueryExecutor
    from app.services.connection_service import ConnectionService
    from app.services.project_service import ProjectService

    from app.core.arricchisci.api_connector import api_connection_service

    from app.core.datasources.service import datasource_service

    project_service = ProjectService()
    connection_service = ConnectionService()
    database_inspector = DatabaseInspector()
    query_generator = QueryGenerator()
    query_executor = QueryExecutor(
        results_dir=settings.DATA_DIR / "temp",
        save_function=save_function,
    )
    features_service = FeaturesService(save_function=save_function)

    orchestrator = OrchestratorService(
        project_service=project_service,
        executor=query_executor,
        generator=query_generator,
        connection_service=connection_service,
        features_service=features_service,
    )

    return RuntimeServices(
        project_service=project_service,
        connection_service=connection_service,
        database_inspector=database_inspector,
        query_generator=query_generator,
        query_executor=query_executor,
        features_service=features_service,
        orchestrator=orchestrator,
        api_connection_service=api_connection_service,
        datasource_service=datasource_service,
    )
