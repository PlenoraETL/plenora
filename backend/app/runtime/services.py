"""Typed runtime composition factory."""

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
    from app.core.connections.service import ConnectionService
    from app.core.projects.service import ProjectService


__all__ = [
    "OrchestratorStack",
    "RuntimeServices",
    "build_orchestrator_stack",
    "build_runtime_services",
]


@dataclass(frozen=True)
class OrchestratorStack:
    """Small runtime bundle for callers that only need pipeline execution."""

    project_service: "ProjectService"
    connection_service: "ConnectionService"
    query_generator: "QueryGenerator"
    query_executor: "QueryExecutor"
    features_service: "FeaturesService"
    orchestrator: "OrchestratorService"


@dataclass(frozen=True)
class RuntimeServices:
    """Frozen typed container for shared service singletons.

    Bundles shared services previously constructed at module-load time in
    `app/main.py`. Each field is a constructed instance, NOT a class.
    Frozen so reassignments surface as errors instead of silently rebinding.

    Field roles:
      `project_service`: project CRUD compatibility module
      - `connection_service`: external DB connection registry.
      - `database_inspector`: introspection over DB connections.
      - `query_generator`: ESTRAI SQL generation pipeline.
      - `query_executor`: raw query execution + result staging.
      - `features_service`: ArcGIS Feature Service consumer.
      - `orchestrator`: pipeline orchestrator (depends on the above 5,
        excluding `database_inspector`).
      - `api_connection_service`: arricchisci API connection singleton.
        IMPORTANT: this field is the SAME object as the canonical
        module-level singleton
        `app.core.arricchisci.api_connector.api_connection_service`.
        The container does not own its construction — `api_connector`
        constructs it eagerly at module import as before. The
        container exposes it for explicit dependency wiring; the
        public import path + the test monkeypatch surface
        (`patch("app.core.arricchisci.api_connector.api_connection_service.<method>", ...)`)
        are preserved. Identity invariant locked by contract test.
      - `datasource_service`: datasources singleton. Same "wrap, don't move"
        strategy as `api_connection_service`: identity-preserving
        compatibility export of the canonical
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


def build_orchestrator_stack(
    *,
    settings: Any,
    save_function: Any,
    results_dir: Any = None,
    connection_service: Any = None,
) -> OrchestratorStack:
    """Construct the shared services required to execute a pipeline.

    This keeps ArcGIS Feature Service construction inside the runtime
    composition boundary while preserving standalone callers such as
    scheduler and arricchisci recompute paths.
    """
    # Lazy imports keep this module cheap to load for contract tests.
    from app.core.estrai.query_generator import QueryGenerator
    from app.core.estrai.executor import EstraiExecutor
    from app.core.features.features_service import FeaturesService
    from app.core.manipola.executor import ManipolaExecutor
    from app.core.arricchisci.executor import EnrichExecutor
    from app.core.orchestrator.service import OrchestratorService
    from app.db.query_executor import QueryExecutor
    from app.core.connections.service import ConnectionService
    from app.core.projects.service import ProjectService

    project_service = ProjectService()
    resolved_connection_service = connection_service or ConnectionService()
    query_generator = QueryGenerator()
    resolved_results_dir = (
        results_dir if results_dir is not None else settings.DATA_DIR / "temp"
    )
    query_executor = QueryExecutor(
        results_dir=resolved_results_dir,
        save_function=save_function,
    )
    features_service = FeaturesService(save_function=save_function)

    orchestrator = None

    def dispatch_pipeline(*args: Any, **kwargs: Any) -> Any:
        if orchestrator is None:
            raise RuntimeError("Orchestrator dispatcher used before wiring")
        return orchestrator.execute_pipeline(*args, **kwargs)

    estrai_executor = EstraiExecutor(
        project_service=project_service,
        executor=query_executor,
        generator=query_generator,
        connection_service=resolved_connection_service,
        features_service=features_service,
    )
    manipola_executor = ManipolaExecutor(
        project_service=project_service,
        pipeline_dispatcher=dispatch_pipeline,
    )
    enrich_executor = EnrichExecutor(project_service=project_service)

    orchestrator = OrchestratorService(
        project_service=project_service,
        executor=query_executor,
        generator=query_generator,
        connection_service=resolved_connection_service,
        features_service=features_service,
        estrai_executor=estrai_executor,
        manipola_executor=manipola_executor,
        enrich_executor=enrich_executor,
    )

    return OrchestratorStack(
        project_service=project_service,
        connection_service=resolved_connection_service,
        query_generator=query_generator,
        query_executor=query_executor,
        features_service=features_service,
        orchestrator=orchestrator,
    )


def build_runtime_services(
    *,
    settings: Any,
    save_function: Any,
) -> RuntimeServices:
    """Construct shared services in dependency order.

    Args:
        settings: app `Settings` singleton. Read for
            `DATA_DIR` (composed into the executor's results dir).
        save_function: dataframe save function injected into
            `QueryExecutor` and `FeaturesService`. Today this is
            `app.core.dataframes.frame_io.save_dataframe`; injected to keep
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
    # Lazy imports keep this module cheap to load for contract tests.
    from app.core.estrai.database_inspector import DatabaseInspector
    from app.core.connections.service import ConnectionService

    from app.core.arricchisci.api_connector import api_connection_service

    from app.core.datasources.service import datasource_service

    connection_service = ConnectionService()
    database_inspector = DatabaseInspector()
    orchestrator_stack = build_orchestrator_stack(
        settings=settings,
        save_function=save_function,
        connection_service=connection_service,
    )

    return RuntimeServices(
        project_service=orchestrator_stack.project_service,
        connection_service=connection_service,
        database_inspector=database_inspector,
        query_generator=orchestrator_stack.query_generator,
        query_executor=orchestrator_stack.query_executor,
        features_service=orchestrator_stack.features_service,
        orchestrator=orchestrator_stack.orchestrator,
        api_connection_service=api_connection_service,
        datasource_service=datasource_service,
    )
