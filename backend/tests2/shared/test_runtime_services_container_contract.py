"""Contract test BE-GO-READY-BOOTSTRAP-ENGINE-011-RUNTIME-CONTAINER-FIRST-SLICE.

Lock the new typed runtime composition boundary:

  - `app.runtime.build_runtime_services(...)` returns a frozen
    `RuntimeServices` dataclass with 7 fields (project + connection +
    database_inspector + query_generator + query_executor + features +
    orchestrator), constructed in dependency order.
  - The orchestrator depends on the 5 prior services. The container
    exposes them as instances, not classes.
  - `app/main.py` consumes the factory and re-exports the resulting
    fields as module-level names for monkeypatch backward compat.
  - `is_bootstrapped()` semantics are not weakened: bootstrap stays
    the gate for runtime-composed services to be usable. A contract
    here proves that the bootstrap completion flag exists and is
    flipped by `bootstrap()` (drift detection: a future refactor
    that decouples the factory from bootstrap must update this lock
    explicitly).

Source-bound where possible to avoid pulling settings/DB into unit
tests. Behavioural tests stub the constructors so the container can
be exercised in isolation.
"""

from __future__ import annotations

from dataclasses import is_dataclass, fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# ---------------------------------------------------------------------------
# 1. Public API shape
# ---------------------------------------------------------------------------


def test_runtime_module_exposes_typed_factory_and_container():
    """Importable surface: `RuntimeServices` dataclass + `build_runtime_services`."""
    from app.runtime import RuntimeServices, build_runtime_services

    assert callable(build_runtime_services)
    assert is_dataclass(RuntimeServices)
    # Frozen dataclass can be detected via the params dict.
    assert RuntimeServices.__dataclass_params__.frozen is True


def test_runtime_services_has_seven_canonical_fields():
    """The container shape locks the first slice (7 fields).

    Adding new fields requires explicit contract update; removing
    one is a regression.
    """
    from app.runtime import RuntimeServices

    field_names = {f.name for f in fields(RuntimeServices)}
    assert field_names == {
        "project_service",
        "connection_service",
        "database_inspector",
        "query_generator",
        "query_executor",
        "features_service",
        "orchestrator",
    }


def test_runtime_services_is_frozen():
    """Reassignment must surface as an error — the container is the
    source of truth, not a mutable scratch space."""
    from app.runtime import RuntimeServices

    instance = RuntimeServices(
        project_service=object(),
        connection_service=object(),
        database_inspector=object(),
        query_generator=object(),
        query_executor=object(),
        features_service=object(),
        orchestrator=object(),
    )
    with pytest.raises(Exception):
        instance.orchestrator = object()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. Factory dependency order — locked behavioural test
# ---------------------------------------------------------------------------


def test_build_runtime_services_constructs_in_canonical_order():
    """The orchestrator MUST receive the 5 prior services as
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

    def _QueryExecutor(*, results_dir, save_function):
        # Locked: kwargs-only signature.
        construction_order.append("QueryExecutor")
        executor_instance.results_dir = results_dir
        executor_instance.save_function = save_function
        return executor_instance

    def _FeaturesService(*, save_function):
        construction_order.append("FeaturesService")
        features_instance.save_function = save_function
        return features_instance

    def _OrchestratorService(*, project_service, executor, generator,
                              connection_service, features_service):
        # Locked: orchestrator receives the 5 services by keyword.
        construction_order.append("OrchestratorService")
        # Identity check: orchestrator gets the EXACT instances built
        # earlier, not new ones. This rejects a refactor that
        # accidentally constructs duplicates.
        assert project_service is project_instance
        assert executor is executor_instance
        assert generator is generator_instance
        assert connection_service is connection_instance
        assert features_service is features_instance
        return orchestrator_instance

    fake_settings = SimpleNamespace(DATA_DIR=Path("/tmp/plenora"))
    fake_save_fn = MagicMock(name="save_dataframe")

    with (
        patch("app.services.project_service.ProjectService", _ProjectService),
        patch("app.services.connection_service.ConnectionService", _ConnectionService),
        patch("app.core.estrai.database_inspector.DatabaseInspector", _DatabaseInspector),
        patch("app.core.estrai.query_generator.QueryGenerator", _QueryGenerator),
        patch("app.db.query_executor.QueryExecutor", _QueryExecutor),
        patch("app.core.features.features_service.FeaturesService", _FeaturesService),
        patch("app.core.orchestrator.service.OrchestratorService", _OrchestratorService),
    ):
        from app.runtime import build_runtime_services

        result = build_runtime_services(
            settings=fake_settings,
            save_function=fake_save_fn,
        )

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

    # 3. Returned container is the typed dataclass with the 7 fields
    # bound to the freshly constructed instances.
    from app.runtime import RuntimeServices

    assert isinstance(result, RuntimeServices)
    assert result.project_service is project_instance
    assert result.connection_service is connection_instance
    assert result.database_inspector is inspector_instance
    assert result.query_generator is generator_instance
    assert result.query_executor is executor_instance
    assert result.features_service is features_instance
    assert result.orchestrator is orchestrator_instance

    # 4. Wiring assertions: settings + save_function flowed through.
    assert executor_instance.results_dir == Path("/tmp/plenora") / "temp"
    assert executor_instance.save_function is fake_save_fn
    assert features_instance.save_function is fake_save_fn


def test_build_runtime_services_kw_only():
    """The factory accepts only keyword arguments. Positional
    invocation must fail."""
    from app.runtime import build_runtime_services

    with pytest.raises(TypeError):
        build_runtime_services(MagicMock(), MagicMock())  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 3. main.py consumes the factory; module-globals re-export from container
# ---------------------------------------------------------------------------


def test_main_py_consumes_runtime_services_factory():
    """Source-bound: `app/main.py` invokes `build_runtime_services`
    instead of constructing the 7 services inline. Drift guard."""
    main_py = (
        Path(__file__).resolve().parent.parent.parent / "app" / "main.py"
    )
    src = main_py.read_text(encoding="utf-8")

    # Factory consumed.
    assert "from app.runtime import build_runtime_services" in src
    assert "build_runtime_services(" in src

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
            f"app/main.py still constructs `{pat.rstrip('(')}` inline. "
            "Construction has moved into "
            "`app.runtime.build_runtime_services`; main.py should "
            "consume the returned container."
        )


def test_main_py_rebinds_globals_from_runtime_container():
    """Source-bound: `main.py` exposes `_runtime_services` and
    re-exports the 7 fields as module-globals (monkeypatch
    backward compat)."""
    main_py = (
        Path(__file__).resolve().parent.parent.parent / "app" / "main.py"
    )
    src = main_py.read_text(encoding="utf-8")

    assert "_runtime_services = build_runtime_services(" in src
    # Each field is rebound at module level.
    for name in (
        "project_service",
        "connection_service",
        "database_inspector",
        "query_generator",
        "query_executor",
        "features_service",
        "orchestrator",
    ):
        assert f"{name} = _runtime_services.{name}" in src, (
            f"main.py must re-export `{name}` from the container "
            "to preserve the test monkeypatch surface."
        )


# ---------------------------------------------------------------------------
# 4. Bootstrap completion semantics — locked
# ---------------------------------------------------------------------------


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
    fake_settings = SimpleNamespace(validate_startup=MagicMock())

    monkeypatch.setattr("app.config.settings", fake_settings)
    monkeypatch.setattr(
        "app.shared.cloud_backends.validate_storage_config",
        MagicMock(),
    )
    monkeypatch.setattr(
        "app.db.db_service.init_all_dbs",
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
        "app.shared.cloud_backends.validate_storage_config",
        MagicMock(),
    )

    def _raise_init():
        raise RuntimeError("DB unreachable")

    monkeypatch.setattr("app.db.db_service.init_all_dbs", _raise_init)

    assert bootstrap_mod.is_bootstrapped() is False
    with pytest.raises(RuntimeError, match="DB unreachable"):
        bootstrap_mod.bootstrap()
    assert bootstrap_mod.is_bootstrapped() is False


# ---------------------------------------------------------------------------
# 5. Module hygiene
# ---------------------------------------------------------------------------


def test_runtime_services_module_does_not_import_fastapi_or_starlette():
    """The factory module must remain leaf: no FastAPI / Starlette
    imports. Tests that exercise the container shape should not pay
    the cost of pulling the web stack."""
    src_path = (
        Path(__file__).resolve().parent.parent.parent
        / "app" / "runtime" / "services.py"
    )
    src = src_path.read_text(encoding="utf-8")
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
