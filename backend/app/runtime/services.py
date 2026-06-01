"""Typed runtime composition factory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.core.arricchisci import api_connector as api_connector_module
from app.core.arricchisci import executor as enrich_executor_module
from app.core.connections import service as connection_service_module
from app.core.datasources import service as datasource_service_module
from app.core.estrai import database_inspector as database_inspector_module
from app.core.estrai import executor as estrai_executor_module
from app.core.estrai import query_generator as query_generator_module
from app.core.manipola import executor as manipola_executor_module
from app.core.orchestrator import service as orchestrator_service_module
from app.core.projects import service as project_service_module
from app.core.query_execution import query_executor as query_executor_module

if TYPE_CHECKING:
    # Lazy at runtime: importing FeaturesService eagerly pulls in the
    # concrete ArcGIS write stack (arcgis_feature_writer/client) into every
    # Celery worker bootstrap. The boundary contract keeps task registration
    # provider-free; the real import lives inside build_orchestrator_stack.
    from app.core.features import features_service as features_service_module


__all__ = [
    "OrchestratorStack",
    "RuntimeServices",
    "build_orchestrator_stack",
    "build_runtime_services",
]


def _register_multilayer_artifact_adapters() -> None:
    """Registra gli adapter verticali (artefatto -> multi-layer) al composition root.

    CARICA (sink ArcGIS multi-layer) resta generico: i formati verticali — IMDF
    ``plenora.indoor.bundle``, futuri export — si registrano QUI, non dentro
    CARICA. La direzione di accoppiamento resta verticale -> orizzontale.
    Idempotente: la registrazione dedupe per tipo. Eseguito a import-time perche'
    ``carica_task`` importa questo modulo al load, prima di ogni write ArcGIS.
    """
    from app.core.imdf import bundle_arcgis_adapter

    bundle_arcgis_adapter.register()


def _register_canvas_node_validators() -> None:
    """Registra i ``validate_node`` dei motori al composition-root (#D).

    L'orchestratore ``canvas_validation`` li scopre via ``validator_for(node.type)``
    senza importarli — la direzione resta motori -> shared. Idempotente.
    """
    from app.core.carica.node_validator import register_carica_validator

    register_carica_validator()


_register_multilayer_artifact_adapters()
_register_canvas_node_validators()


@dataclass(frozen=True)
class OrchestratorStack:
    """Small runtime bundle for callers that only need pipeline execution."""

    project_service: project_service_module.ProjectService
    connection_service: connection_service_module.ConnectionService
    query_generator: query_generator_module.QueryGenerator
    query_executor: query_executor_module.QueryExecutor
    features_service: features_service_module.FeaturesService
    orchestrator: orchestrator_service_module.OrchestratorService


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

    project_service: project_service_module.ProjectService
    connection_service: connection_service_module.ConnectionService
    database_inspector: database_inspector_module.DatabaseInspector
    query_generator: query_generator_module.QueryGenerator
    query_executor: query_executor_module.QueryExecutor
    features_service: features_service_module.FeaturesService
    orchestrator: orchestrator_service_module.OrchestratorService
    api_connection_service: api_connector_module.ApiConnectionService
    datasource_service: datasource_service_module.DataSourceService


def build_orchestrator_stack(
    *,
    connection_service: Any = None,
) -> OrchestratorStack:
    """Construct the shared services required to execute a pipeline.

    This keeps ArcGIS Feature Service construction inside the runtime
    composition boundary while preserving standalone callers such as
    scheduler and arricchisci recompute paths.
    """
    project_service = project_service_module.ProjectService()
    resolved_connection_service = (
        connection_service or connection_service_module.ConnectionService()
    )
    query_generator = query_generator_module.QueryGenerator()
    query_executor = query_executor_module.QueryExecutor()
    from app.core.features import features_service as features_service_module
    features_service = features_service_module.FeaturesService()

    orchestrator = None

    def dispatch_pipeline(*args: Any, **kwargs: Any) -> Any:
        if orchestrator is None:
            raise RuntimeError("Orchestrator dispatcher used before wiring")
        return orchestrator.execute_pipeline(*args, **kwargs)

    estrai_executor = estrai_executor_module.EstraiExecutor(
        project_service=project_service,
        executor=query_executor,
        generator=query_generator,
        connection_service=resolved_connection_service,
        features_service=features_service,
    )
    manipola_executor = manipola_executor_module.ManipolaExecutor(
        project_service=project_service,
        pipeline_dispatcher=dispatch_pipeline,
    )
    enrich_executor = enrich_executor_module.EnrichExecutor(
        project_service=project_service
    )

    orchestrator = orchestrator_service_module.OrchestratorService(
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


def build_runtime_services() -> RuntimeServices:
    """Construct shared services in dependency order.

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
    connection_service = connection_service_module.ConnectionService()
    database_inspector = database_inspector_module.DatabaseInspector()
    orchestrator_stack = build_orchestrator_stack(
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
        api_connection_service=api_connector_module.api_connection_service,
        datasource_service=datasource_service_module.datasource_service,
    )
