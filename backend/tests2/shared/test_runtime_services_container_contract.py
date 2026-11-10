"""
Contract test BE-GO-READY-BOOTSTRAP-ENGINE-011-RUNTIME-CONTAINER-FIRST-SLICE.

Lock the new typed runtime composition boundary:
"""

from __future__ import annotations

from dataclasses import is_dataclass, fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests2.shared._ast_identifiers import imports_symbol_from


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_MAIN_PY = _BACKEND_ROOT / "app" / "main.py"
_APP_FACTORY_PY = _BACKEND_ROOT / "app" / "app_factory.py"
_RUNTIME_SERVICES_PY = _BACKEND_ROOT / "app" / "runtime" / "services.py"
_RUNTIME_FIELD_NAMES = (
    "project_service",
    "connection_service",
    "database_inspector",
    "query_generator",
    "query_executor",
    "features_service",
    "orchestrator",
    "api_connection_service",
    "datasource_service",
)
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _build_runtime_for_contract():
    from app.runtime import build_runtime_services

    return build_runtime_services()


# 1. Public API shape


def test_runtime_module_exposes_typed_factory_and_container():
    """Importable surface: `RuntimeServices` dataclass + `build_runtime_services`."""
    from app.runtime import RuntimeServices, build_runtime_services

    assert callable(build_runtime_services)
    assert is_dataclass(RuntimeServices)
    # Frozen dataclass can be detected via the params dict.
    assert RuntimeServices.__dataclass_params__.frozen is True


def test_runtime_services_has_nine_canonical_fields():
    """The container shape locks the post-017 slice (9 fields).

    Adding new fields requires explicit contract update; removing
    one is a regression.

    History:
      - 011 (first slice): 7 fields (project, connection, inspector,
        generator, executor, features, orchestrator).
      - 016: +1 `api_connection_service` (arricchisci singleton).
      - 017 (this batch): +1 `datasource_service` (datasources
        singleton).
    """
    from app.runtime import RuntimeServices

    assert {f.name for f in fields(RuntimeServices)} == set(_RUNTIME_FIELD_NAMES)


def test_runtime_services_is_frozen():
    """Reassignment must surface as an error — the container is the
    source of truth, not a mutable scratch space."""
    from app.runtime import RuntimeServices

    instance = RuntimeServices(**{name: object() for name in _RUNTIME_FIELD_NAMES})
    for name in ("orchestrator", "api_connection_service", "datasource_service"):
        with pytest.raises(Exception):
            setattr(instance, name, object())


# 2. Factory dependency order — locked behavioural test


def test_build_runtime_services_constructs_in_canonical_order():
    """The orchestrator MUST receive prior services and executors as
    keyword arguments. Locking via MagicMock-based capture ensures
    that a future refactor reordering construction would surface as
    a missing-kwarg `TypeError` or as a divergent assertion here.
    """
    construction_order: list[str] = []

    project_instance = MagicMock(name="ProjectService_inst")
    connection_instance = MagicMock(name="ConnectionService_inst")
    inspector_instance = MagicMock(name="DatabaseInspector_inst")
    generator_instance = MagicMock(name="QueryGenerator_inst")
    executor_instance = MagicMock(name="QueryExecutor_inst")
    features_instance = MagicMock(name="FeaturesService_inst")
    estrai_executor_instance = MagicMock(name="EstraiExecutor_inst")
    manipola_executor_instance = MagicMock(name="ManipolaExecutor_inst")
    enrich_executor_instance = MagicMock(name="EnrichExecutor_inst")
    orchestrator_instance = MagicMock(name="OrchestratorService_inst")

    def _ProjectService():
        construction_order.append("ProjectService")
        return project_instance

    def _ConnectionService():
        construction_order.append("ConnectionService")
        return connection_instance

    def _DatabaseInspector():
        construction_order.append("DatabaseInspector")
        return inspector_instance

    def _QueryGenerator():
        construction_order.append("QueryGenerator")
        return generator_instance

    def _QueryExecutor():
        construction_order.append("QueryExecutor")
        return executor_instance

    def _FeaturesService():
        construction_order.append("FeaturesService")
        return features_instance

    def _EstraiExecutor(*, project_service, executor, generator,
                        connection_service, features_service):
        construction_order.append("EstraiExecutor")
        assert project_service is project_instance
        assert executor is executor_instance
        assert generator is generator_instance
        assert connection_service is connection_instance
        assert features_service is features_instance
        return estrai_executor_instance

    def _ManipolaExecutor(*, project_service, pipeline_dispatcher, isolated_dispatcher):
        construction_order.append("ManipolaExecutor")
        assert project_service is project_instance
        assert callable(pipeline_dispatcher)
        # Ricalcolo isolato a monte («Utente che esegue», runtime_identity U3).
        assert callable(isolated_dispatcher)
        manipola_executor_instance.pipeline_dispatcher = pipeline_dispatcher
        return manipola_executor_instance

    def _EnrichExecutor(*, project_service):
        construction_order.append("EnrichExecutor")
        assert project_service is project_instance
        return enrich_executor_instance

    def _OrchestratorService(*, project_service, executor, generator,
                              connection_service, features_service,
                              estrai_executor, manipola_executor,
                              enrich_executor):
        # Locked: orchestrator receives services and executor deps by keyword.
        construction_order.append("OrchestratorService")
        # Identity check: orchestrator gets the EXACT instances built
        # earlier, not new ones. This rejects a refactor that
        # accidentally constructs duplicates.
        assert project_service is project_instance
        assert executor is executor_instance
        assert generator is generator_instance
        assert connection_service is connection_instance
        assert features_service is features_instance
        assert estrai_executor is estrai_executor_instance
        assert manipola_executor is manipola_executor_instance
        assert enrich_executor is enrich_executor_instance
        return orchestrator_instance

    # BE-GO-READY-BOOTSTRAP-ENGINE-016: the factory imports the
    # canonical singleton from `api_connector` instead of constructing
    # one. Stub it with an identifiable sentinel so the identity
    # invariant is locked.
    api_conn_sentinel = MagicMock(name="ApiConnectionService_singleton")
    # BE-GO-READY-BOOTSTRAP-ENGINE-017: same wrap-don't-move pattern
    # for `datasource_service`.
    datasource_sentinel = MagicMock(name="DataSourceService_singleton")

    with (
        patch("app.core.projects.service.ProjectService", _ProjectService),
        patch("app.core.connections.service.ConnectionService", _ConnectionService),
        patch("app.core.estrai.database_inspector.DatabaseInspector", _DatabaseInspector),
        patch("app.core.estrai.query_generator.QueryGenerator", _QueryGenerator),
        patch("app.core.query_execution.query_executor.QueryExecutor", _QueryExecutor),
        patch("app.core.features.features_service.FeaturesService", _FeaturesService),
        patch("app.core.estrai.executor.EstraiExecutor", _EstraiExecutor),
        patch("app.core.manipola.executor.ManipolaExecutor", _ManipolaExecutor),
        patch("app.core.arricchisci.executor.EnrichExecutor", _EnrichExecutor),
        patch("app.core.orchestrator.service.OrchestratorService", _OrchestratorService),
        patch(
            "app.core.arricchisci.api_connector.api_connection_service",
            api_conn_sentinel,
        ),
        patch(
            "app.core.datasources.service.datasource_service",
            datasource_sentinel,
        ),
    ):
        from app.runtime import build_runtime_services

        result = build_runtime_services()

    # 1. Order: orchestrator MUST be last (it depends on all the others).
    assert construction_order[-1] == "OrchestratorService"
    # 2. The 5 dependencies of the orchestrator must come BEFORE it.
    for dep in (
        "ProjectService",
        "ConnectionService",
        "QueryGenerator",
        "QueryExecutor",
        "FeaturesService",
    ):
        assert construction_order.index(dep) < construction_order.index(
            "OrchestratorService"
        ), f"{dep} must be constructed before OrchestratorService"

    # 3. Returned container is the typed dataclass with the 8 fields
    # bound to the freshly constructed instances + the canonical
    # singleton imported from `api_connector`.
    from app.runtime import RuntimeServices

    assert isinstance(result, RuntimeServices)
    assert result.project_service is project_instance
    assert result.connection_service is connection_instance
    assert result.database_inspector is inspector_instance
    assert result.query_generator is generator_instance
    assert result.query_executor is executor_instance
    assert result.features_service is features_instance
    assert result.orchestrator is orchestrator_instance
    # IDENTITY INVARIANT (016): the container does NOT construct a
    # second ApiConnectionService — it must consume the canonical
    # `api_connector.api_connection_service` singleton.
    assert result.api_connection_service is api_conn_sentinel
    # IDENTITY INVARIANT (017): same for DataSourceService — the
    # container must consume the canonical singleton, not construct
    # a duplicate.
    assert result.datasource_service is datasource_sentinel

    # 4. The factory MUST NOT also call `ApiConnectionService()` or
    # `DataSourceService()`. Constructing a second instance would
    # diverge from the canonical singleton and break any test patch
    # surface that targets the canonical attribute. Construction
    # count tracked above does not include either class because the
    # factory imports the already-constructed singletons instead of
    # calling the classes.
    assert "ApiConnectionService" not in construction_order, (
        "build_runtime_services must NOT construct ApiConnectionService; "
        "it must import the canonical singleton from "
        "`app.core.arricchisci.api_connector.api_connection_service`."
    )
    assert "DataSourceService" not in construction_order, (
        "build_runtime_services must NOT construct DataSourceService; "
        "it must import the canonical singleton from "
        "`app.core.datasources.service.datasource_service`."
    )


def test_build_runtime_services_kw_only():
    """The factory accepts only keyword arguments. Positional
    invocation must fail."""
    from app.runtime import build_runtime_services

    with pytest.raises(TypeError):
        build_runtime_services(MagicMock())  # type: ignore[misc]


# 3. app_factory consumes the runtime-services factory


def test_app_factory_consumes_runtime_services_factory():
    """Source-bound: `app/app_factory.py` invokes `build_runtime_services`
    instead of constructing shared services inline. Drift guard."""
    src = _read(_APP_FACTORY_PY)

    # Factory consumed.
    assert imports_symbol_from(src, "app.runtime", "build_runtime_services")
    assert "resolved_deps.build_runtime_services(" in src

    # Module-globals are still defined (monkeypatch surface), but
    # they MUST be derived from the container — not constructed
    # inline. Reject the previous inline form.
    forbidden_inline = (
        "project_service = ProjectService()",
        "connection_service = ConnectionService()",
        "database_inspector = DatabaseInspector()",
        "query_generator = QueryGenerator()",
        "query_executor = QueryExecutor(",
        "features_service = FeaturesService(",
        "orchestrator = OrchestratorService(",
    )
    for pat in forbidden_inline:
        assert pat not in src, (
            f"app/app_factory.py still constructs `{pat.rstrip('(')}` inline. "
            "Construction has moved into "
            "`app.runtime.build_runtime_services`; app_factory.py should "
            "consume the returned runtime container."
        )


def test_main_py_keeps_only_asgi_app_surface():
    """Source-bound: `main.py` must not re-export runtime service globals."""
    src = _read(_MAIN_PY)

    assert "app = create_app()" in src
    assert "_runtime_services = app.state.runtime_services" not in src
    for name in _RUNTIME_FIELD_NAMES:
        assert f"{name} = _runtime_services.{name}" not in src, (
            f"main.py must not re-export `{name}` from the container."
        )


# 4. Bootstrap completion semantics — locked


def test_bootstrap_completion_flag_exists_and_is_initially_false():
    """`is_bootstrapped()` must exist and start False. Used by
    test harnesses + future readiness consumers as the canonical
    indicator that the four-step bootstrap boundary has been
    crossed."""
    import importlib

    import app.bootstrap as bootstrap_mod

    # Force a fresh import to observe the initial flag value, since
    # other tests in the session may have already crossed bootstrap.
    importlib.reload(bootstrap_mod)
    assert hasattr(bootstrap_mod, "is_bootstrapped")
    assert callable(bootstrap_mod.is_bootstrapped)
    assert bootstrap_mod.is_bootstrapped() is False


def test_bootstrap_flips_completion_flag_only_on_success(monkeypatch):
    """`bootstrap()` must flip `is_bootstrapped()` to True ONLY
    after all four steps have returned. A failure in any step
    leaves the flag at False."""
    import importlib

    import app.bootstrap as bootstrap_mod
    importlib.reload(bootstrap_mod)

    # Stub the four step entry points.
    fake_settings = SimpleNamespace()

    monkeypatch.setattr("app.config.settings", fake_settings)
    monkeypatch.setattr(
        "app.startup_validation.validate_startup",
        MagicMock(),
    )
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.validate_storage_config",
        MagicMock(),
    )
    monkeypatch.setattr(
        "app.core.runtime.db_init.init_all_dbs",
        MagicMock(),
    )

    # Sanity: pre-call.
    assert bootstrap_mod.is_bootstrapped() is False

    bootstrap_mod.bootstrap()

    assert bootstrap_mod.is_bootstrapped() is True

    # Failure mode: simulate a 3rd-step failure on a fresh
    # bootstrap. The flag should NOT flip from False back to True.
    importlib.reload(bootstrap_mod)
    monkeypatch.setattr("app.config.settings", fake_settings)
    monkeypatch.setattr(
        "app.startup_validation.validate_startup",
        MagicMock(),
    )
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.validate_storage_config",
        MagicMock(),
    )

    def _raise_init():
        raise RuntimeError("DB unreachable")

    monkeypatch.setattr("app.core.runtime.db_init.init_all_dbs", _raise_init)

    assert bootstrap_mod.is_bootstrapped() is False
    with pytest.raises(RuntimeError, match="DB unreachable"):
        bootstrap_mod.bootstrap()
    assert bootstrap_mod.is_bootstrapped() is False


# 5. Module hygiene


def test_runtime_services_module_does_not_import_fastapi_or_starlette():
    """The factory module must remain leaf: no FastAPI / Starlette
    imports. Tests that exercise the container shape should not pay
    the cost of pulling the web stack."""
    src = _read(_RUNTIME_SERVICES_PY)
    forbidden = ("fastapi", "starlette")
    for line in src.splitlines():
        stripped = line.lstrip()
        if not (stripped.startswith("import ") or stripped.startswith("from ")):
            continue
        for f in forbidden:
            assert f not in line.lower(), (
                f"app/runtime/services.py imports '{f}' on line: {line!r}. "
                "The factory must stay leaf."
            )


# 6. BE-GO-READY-BOOTSTRAP-ENGINE-016 — api_connection_service slice


def test_runtime_container_owns_api_connection_service_field():
    """The 8th field exists, is typed, and references the canonical
    arricchisci singleton — NOT a fresh construction."""
    import app.core.arricchisci.api_connector as api_connector_mod
    from app.runtime import RuntimeServices

    container = _build_runtime_for_contract()
    # Identity invariant: the field is the SAME object as the
    # canonical module-level singleton. Any test that
    # `patch("app.core.arricchisci.api_connector.api_connection_service.<method>", ...)`
    # therefore patches the SAME instance the container exposes.
    assert isinstance(container, RuntimeServices)
    assert container.api_connection_service is api_connector_mod.api_connection_service


def test_main_py_no_longer_imports_api_connection_service_directly():
    """Source-bound: `app/main.py` must NOT import
    `api_connection_service` directly from `api_connector` — the
    canonical path is the runtime container.

    The arricchisci singleton stays in `api_connector.py` for its own
    patch surface, but `main.py` stays ASGI-only.
    """
    src = _read(_MAIN_PY)
    forbidden_import = (
        "from app.core.arricchisci.api_connector import api_connection_service"
    )
    assert forbidden_import not in src, (
        "app/main.py still imports `api_connection_service` directly "
        "from `api_connector`. The container owns the field; "
        "main.py should stay ASGI-only."
    )


def test_api_connector_module_still_exposes_singleton_for_patch_surface():
    """The canonical module-level singleton MUST still exist on
    `app.core.arricchisci.api_connector`. Removing it would break
    every existing test that uses
    `patch("app.core.arricchisci.api_connector.api_connection_service.<method>", ...)`.

    Locked here because the slice approach is "wrap, don't move".
    """
    import app.core.arricchisci.api_connector as api_connector_mod

    assert hasattr(api_connector_mod, "api_connection_service"), (
        "Canonical singleton path missing: "
        "app.core.arricchisci.api_connector.api_connection_service. "
        "BE-GO-READY-BOOTSTRAP-ENGINE-016 keeps construction in "
        "`api_connector` to preserve the test patch surface."
    )


def test_factory_does_not_reconstruct_api_connection_service():
    """The factory MUST NOT call `ApiConnectionService()` on its own
    — it must consume the canonical singleton via import. A second
    construction would produce a divergent instance, breaking every
    `patch("...api_connection_service.<method>", ...)` test.
    """
    src = _read(_RUNTIME_SERVICES_PY)
    # Reject any line that constructs ApiConnectionService inline.
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"'):
            continue
        assert "ApiConnectionService(" not in line, (
            f"app/runtime/services.py constructs ApiConnectionService "
            f"on line: {line!r}. Use the canonical singleton attribute "
            "from `app.core.arricchisci.api_connector` instead."
        )
    # Positive assertion: the factory consumes the singleton from the
    # canonical module attribute, preserving patchability while avoiding
    # direct symbol binding at import time.
    assert (
        "from app.core.arricchisci import api_connector as api_connector_module"
        in src
        and "api_connector_module.api_connection_service" in src
    ), (
        "app/runtime/services.py must consume the canonical singleton "
        "module attribute to preserve the public/patch surface."
    )


# 7. BE-GO-READY-BOOTSTRAP-ENGINE-017 — datasource_service slice


def test_runtime_container_owns_datasource_service_field():
    """The 9th field exists, is typed, and references the canonical
    datasources singleton — NOT a fresh construction."""
    import app.core.datasources.service as ds_service_mod
    from app.runtime import RuntimeServices

    container = _build_runtime_for_contract()
    # Identity invariant: same object as the canonical module
    # singleton. A future test that does
    # `patch("app.core.datasources.service.datasource_service.<m>", ...)`
    # would patch the SAME instance the container exposes.
    assert isinstance(container, RuntimeServices)
    assert container.datasource_service is ds_service_mod.datasource_service


def test_main_py_no_longer_imports_datasource_service_directly():
    """Source-bound: `app/main.py` must NOT import `datasource_service`
    directly from `app.core.datasources.service` — the canonical path
    is the runtime container.

    The singleton stays in `service.py:440` (canonical attribute
    preserved); `main.py` stays ASGI-only.
    """
    src = _read(_MAIN_PY)
    forbidden_import = (
        "from app.core.datasources.service import datasource_service"
    )
    assert forbidden_import not in src, (
        "app/main.py still imports `datasource_service` directly "
        "from `app.core.datasources.service`. The container owns the field; "
        "main.py should stay ASGI-only."
    )


def test_datasources_service_module_still_exposes_singleton_for_patch_surface():
    """The canonical module-level singleton MUST still exist on
    `app.core.datasources.service`. Removing it would break any
    future test or consumer that patches the canonical attribute.

    Locked here because the slice approach is "wrap, don't move".
    """
    import app.core.datasources.service as ds_service_mod

    assert hasattr(ds_service_mod, "datasource_service"), (
        "Canonical singleton path missing: "
        "app.core.datasources.service.datasource_service. "
        "BE-GO-READY-BOOTSTRAP-ENGINE-017 keeps construction in "
        "`service.py` to preserve the public attribute surface."
    )


def test_factory_does_not_reconstruct_datasource_service():
    """The factory MUST NOT call `DataSourceService()` on its own —
    it must consume the canonical singleton via import. A second
    construction would produce a divergent instance, breaking the
    identity invariant.
    """
    src = _read(_RUNTIME_SERVICES_PY)
    # Reject any line that constructs DataSourceService inline.
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"'):
            continue
        assert "DataSourceService(" not in line, (
            f"app/runtime/services.py constructs DataSourceService "
            f"on line: {line!r}. Use the canonical singleton attribute "
            "from `app.core.datasources.service` instead."
        )
    # Positive assertion: the factory consumes the singleton from the
    # canonical module attribute, preserving patchability while avoiding
    # direct symbol binding at import time.
    assert (
        "from app.core.datasources import service as datasource_service_module"
        in src
        and "datasource_service_module.datasource_service" in src
    ), (
        "app/runtime/services.py must import the canonical "
        "datasource_service module attribute to preserve the public surface."
    )
