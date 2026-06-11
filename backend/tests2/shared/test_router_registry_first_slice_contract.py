"""
Contract test BE-GO-READY-BOOTSTRAP-ENGINE-018-ROUTER-REGISTRY-FIRST-SLICE.

Lock the typed router-init binding registry without repeating the same
slice checks for every migrated router.
"""

from __future__ import annotations

import ast
import importlib
from dataclasses import is_dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[2]


BINDING_CASES = [
    pytest.param(
        "ORCHESTRATOR_BINDING",
        "orchestrator",
        ("orchestrator",),
        "app.core.orchestrator.router",
        "init_orchestrator",
        "init_orchestrator(orchestrator)",
        id="orchestrator",
    ),
    pytest.param(
        "CONNECTIONS_BINDING",
        "connections",
        ("connection_service", "database_inspector"),
        "app.core.connections.router",
        "init_connections",
        "init_connections(connection_service, database_inspector)",
        id="connections",
    ),
    pytest.param(
        "PROJECTS_BINDING",
        "projects",
        ("project_service",),
        "app.core.projects.router",
        "init_projects",
        "init_projects(project_service)",
        id="projects",
    ),
    pytest.param(
        "ARRICCHISCI_BINDING",
        "arricchisci",
        ("project_service", "api_connection_service"),
        "app.core.arricchisci.router",
        "init_arricchisci",
        "init_arricchisci(project_service, api_connection_service)",
        id="arricchisci",
    ),
    pytest.param(
        "DATASOURCES_BINDING",
        "datasources",
        ("datasource_service",),
        "app.core.datasources.router",
        "init_datasources",
        "init_datasources(datasource_service)",
        id="datasources",
    ),
    pytest.param(
        "CARICA_BINDING",
        "carica",
        ("connection_service",),
        "app.core.carica.router",
        "init_carica_service",
        "init_carica_service(connection_service)",
        id="carica",
    ),
    pytest.param(
        "ESTRAI_BINDING",
        "estrai",
        ("project_service", "connection_service", "query_executor"),
        "app.core.estrai.router",
        "init_estrai",
        "init_estrai(project_service, connection_service, query_executor)",
        id="estrai",
    ),
]


def _read_source(rel_path: str) -> str:
    return (_BACKEND_ROOT / rel_path).read_text(encoding="utf-8")


def _runtime_binding(binding_attr: str):
    return getattr(importlib.import_module("app.runtime"), binding_attr)


def _sentinel_services(field_names: tuple[str, ...]):
    sentinels = tuple(object() for _ in field_names)
    return SimpleNamespace(**dict(zip(field_names, sentinels))), sentinels


def _imported_names(src: str) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.asname or alias.name)
    return imported


def _top_level_import_modules(src: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.parse(src).body:
        if isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_runtime_module_exposes_registry_surface():
    """Importable public surface for the registry and every migrated binding."""
    runtime = importlib.import_module("app.runtime")

    assert is_dataclass(runtime.RouterBinding)
    assert runtime.RouterBinding.__dataclass_params__.frozen is True
    assert callable(runtime.bind_router_services)
    for binding_attr, *_ in [case.values for case in BINDING_CASES]:
        assert isinstance(getattr(runtime, binding_attr), runtime.RouterBinding)


def test_router_binding_fields_are_typed_and_minimal():
    """The dataclass has exactly 3 fields: name, init_fn, extract."""
    from dataclasses import fields

    from app.runtime.router_registry import RouterBinding

    assert {f.name for f in fields(RouterBinding)} == {"name", "init_fn", "extract"}


@pytest.mark.parametrize(
    "binding_attr, stable_name, field_names, router_module, init_name, bad_call",
    BINDING_CASES,
)
def test_binding_names_are_stable(
    binding_attr, stable_name, field_names, router_module, init_name, bad_call
):
    assert _runtime_binding(binding_attr).name == stable_name


@pytest.mark.parametrize(
    "binding_attr, stable_name, field_names, router_module, init_name, bad_call",
    BINDING_CASES,
)
def test_extract_returns_configured_service_fields_by_identity(
    binding_attr, stable_name, field_names, router_module, init_name, bad_call
):
    """Each binding extracts the expected service fields in positional order."""
    services, sentinels = _sentinel_services(field_names)

    args = _runtime_binding(binding_attr).extract(services)

    assert isinstance(args, tuple)
    assert args == sentinels
    for actual, expected in zip(args, sentinels):
        assert actual is expected


@pytest.mark.parametrize(
    "binding_attr, stable_name, field_names, router_module, init_name, bad_call",
    BINDING_CASES,
)
def test_init_fn_invokes_router_module_init_with_positional_args(
    monkeypatch, binding_attr, stable_name, field_names, router_module, init_name, bad_call
):
    """The lazy thunk resolves the router module init function and forwards args."""
    binding = _runtime_binding(binding_attr)
    router_mod = importlib.import_module(router_module)
    sentinels = tuple(object() for _ in field_names)
    captured: list[tuple[object, ...]] = []

    def _fake_init(*args):
        captured.append(args)

    monkeypatch.setattr(router_mod, init_name, _fake_init)

    binding.init_fn(*sentinels)

    assert captured == [sentinels]


def test_bind_router_services_calls_init_fn_exactly_once_with_extracted_args():
    """Dispatcher contract: extract tuple is splatted positionally, no kwargs."""
    from app.runtime import RouterBinding, bind_router_services

    init_calls: list[tuple[tuple[object, ...], dict]] = []
    sentinel = object()
    binding = RouterBinding(
        name="test_only",
        init_fn=lambda *args, **kwargs: init_calls.append((args, kwargs)),
        extract=lambda services: (sentinel,),
    )

    result = bind_router_services(SimpleNamespace(), binding)

    assert result is None
    assert init_calls == [((sentinel,), {})]


@pytest.mark.parametrize(
    "binding_attr, stable_name, field_names, router_module, init_name, bad_call",
    BINDING_CASES,
)
def test_bind_router_services_passes_container_owned_instances_by_identity(
    binding_attr, stable_name, field_names, router_module, init_name, bad_call
):
    """The dispatcher passes extracted container objects without rebuilding them."""
    from app.runtime import bind_router_services

    binding = _runtime_binding(binding_attr)
    services, sentinels = _sentinel_services(field_names)
    captured: list[tuple[object, ...]] = []
    spy_binding = type(binding)(
        name=binding.name,
        init_fn=lambda *args: captured.append(args),
        extract=binding.extract,
    )

    bind_router_services(services, spy_binding)

    assert captured == [sentinels]
    for actual, expected in zip(captured[0], sentinels):
        assert actual is expected


@pytest.mark.parametrize(
    "binding_attr, stable_name, field_names, router_module, init_name, bad_call",
    BINDING_CASES,
)
def test_bind_router_services_attribute_error_on_missing_field_propagates(
    binding_attr, stable_name, field_names, router_module, init_name, bad_call
):
    """Missing service fields fail at startup via AttributeError."""
    from app.runtime import bind_router_services

    present_fields = field_names[:-1]
    incomplete_services = SimpleNamespace(
        **{field_name: object() for field_name in present_fields}
    )

    with pytest.raises(AttributeError):
        bind_router_services(incomplete_services, _runtime_binding(binding_attr))


@pytest.mark.parametrize(
    "binding_attr, field_names, drift_init",
    [
        pytest.param("PROJECTS_BINDING", ("project_service",), lambda a, b: None, id="1arg"),
        pytest.param(
            "CONNECTIONS_BINDING",
            ("connection_service", "database_inspector"),
            lambda _: None,
            id="2arg",
        ),
    ],
)
def test_bind_router_services_type_error_on_signature_drift(
    binding_attr, field_names, drift_init
):
    """Representatives for 1-arg and 2-arg bindings fail fast on arity drift."""
    from app.runtime import bind_router_services

    binding = _runtime_binding(binding_attr)
    services, _ = _sentinel_services(field_names)
    drift_binding = type(binding)(
        name=binding.name,
        init_fn=drift_init,
        extract=binding.extract,
    )

    with pytest.raises(TypeError):
        bind_router_services(services, drift_binding)


@pytest.mark.parametrize(
    "forbidden_module",
    ["fastapi", "boto3", "psycopg2", "redis"],
)
def test_router_registry_module_is_import_cheap(forbidden_module):
    """The registry module must not import heavy service/router graphs eagerly."""
    top_level_roots = {
        module.split(".")[0]
        for module in _top_level_import_modules(_read_source("app/runtime/router_registry.py"))
    }

    assert forbidden_module not in top_level_roots, (
        f"app/runtime/router_registry.py must not import "
        f"{forbidden_module} at top level"
    )


def test_router_registry_does_not_import_router_modules_at_top_level():
    """Router imports must stay inside each lazy binding thunk."""
    top_level_modules = _top_level_import_modules(_read_source("app/runtime/router_registry.py"))
    forbidden_fragments = {
        router_module.removeprefix("app.core.")
        for _, _, _, router_module, _, _ in [case.values for case in BINDING_CASES]
    }

    violations = [
        module
        for module in top_level_modules
        if any(fragment in module for fragment in forbidden_fragments)
    ]

    assert not violations, (
        "app/runtime/router_registry.py must import router modules lazily, "
        f"not at module top: {violations}"
    )


def test_app_factory_imports_all_bindings_and_dispatcher():
    """Positive lock: app factory imports every binding and invokes dispatcher."""
    from tests2.shared._ast_identifiers import all_identifiers

    src = _read_source("app/app_factory.py")
    ids = all_identifiers(src)

    for binding_attr, *_ in [case.values for case in BINDING_CASES]:
        assert binding_attr in ids, f"app/app_factory.py must import {binding_attr}"
    assert "bind_router_services" in ids, (
        "app/app_factory.py must invoke bind_router_services()"
    )


def test_app_factory_does_not_import_router_init_symbols():
    """Router-side init symbols must no longer appear in app_factory imports."""
    imported = _imported_names(_read_source("app/app_factory.py"))
    forbidden = {init_name for _, _, _, _, init_name, _ in [case.values for case in BINDING_CASES]}

    assert forbidden.isdisjoint(imported), (
        "app/app_factory.py must not import router init functions directly: "
        f"{sorted(forbidden & imported)}"
    )


def test_app_factory_has_no_legacy_direct_router_init_literals():
    """Exact old procedural call shapes stay out of app/app_factory.py."""
    src = _read_source("app/app_factory.py")

    for binding_attr, _, _, _, init_name, bad_call in [case.values for case in BINDING_CASES]:
        # RAW: bad_call is a full call-expression pattern (e.g. "init_orchestrator(orchestrator)"), not a plain identifier
        assert bad_call not in src, (
            "app/app_factory.py should call router init through "
            f"bind_router_services(runtime_services, {binding_attr}) "
            f"instead of {bad_call}"
        )


def test_app_factory_init_block_has_no_procedural_router_init_calls():
    """There must be zero procedural router-side init calls in app_factory.py."""
    tree = ast.parse(_read_source("app/app_factory.py"))
    forbidden_router_inits = {
        init_name for _, _, _, _, init_name, _ in [case.values for case in BINDING_CASES]
    }

    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in forbidden_router_inits:
                violations.append((node.lineno, node.func.id))

    assert not violations, (
        "app/app_factory.py still contains procedural router-side init calls:\n"
        + "\n".join(f"  line {ln}: {name}(...)" for ln, name in violations)
        + "\nRoute through bind_router_services(_runtime_services, <NAME>_BINDING)."
    )


@pytest.mark.parametrize(
    "router_path",
    [
        pytest.param(
            router_module.replace(".", "/") + ".py",
            id=stable_name,
        )
        for _, stable_name, _, router_module, _, _ in [case.values for case in BINDING_CASES]
    ],
)
def test_routers_do_not_import_from_main(router_path):
    """Composition flows main -> runtime -> router, never router -> main."""
    tree = ast.parse(_read_source(router_path))

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "app.main" or module.startswith("app.main."):
                violations.append((node.lineno, module))

    assert not violations, (
        f"{router_path} must not import from app.main: {violations}"
    )


def test_app_factory_init_order_is_canonical():
    """Source-bound startup order lock for the 6 typed bindings."""
    src = _read_source("app/app_factory.py")
    markers = [
        ("CONNECTIONS_BINDING", "bind_router_services(runtime_services, CONNECTIONS_BINDING)"),
        ("PROJECTS_BINDING", "bind_router_services(runtime_services, PROJECTS_BINDING)"),
        ("ARRICCHISCI_BINDING", "bind_router_services(runtime_services, ARRICCHISCI_BINDING)"),
        ("DATASOURCES_BINDING", "bind_router_services(runtime_services, DATASOURCES_BINDING)"),
        ("CARICA_BINDING", "bind_router_services(runtime_services, CARICA_BINDING)"),
        ("ORCHESTRATOR_BINDING", "bind_router_services(runtime_services, ORCHESTRATOR_BINDING)"),
    ]

    positions = []
    for label, literal in markers:
        # RAW: literal is a full call-expression pattern (e.g. "bind_router_services(runtime_services, CONNECTIONS_BINDING)"), not a plain identifier
        idx = src.find(literal)
        assert idx >= 0, (
            f"Expected `{literal}` in app/app_factory.py; bootstrap order "
            f"marker `{label}` is missing or changed shape."
        )
        positions.append((label, idx))

    actual_indices = [idx for _, idx in positions]
    assert actual_indices == sorted(actual_indices), (
        "Bootstrap init order regression in app/main.py.\n"
        f"  Expected canonical order: {[label for label, _ in markers]}\n"
        f"  Got positional order:     {[label for label, _ in positions]}\n"
        "Reordering breaks runtime invariants: arricchisci needs "
        "project_service init first; carica consumes connection_service; "
        "orchestrator must run last."
    )


def test_datasources_binding_uses_runtime_container_singleton_identity():
    """Datasources keeps the canonical singleton through container and binding."""
    import app.core.datasources.service as datasources_svc_mod
    from app.runtime import DATASOURCES_BINDING
    from app.runtime.services import build_runtime_services

    services = build_runtime_services()

    canonical = datasources_svc_mod.datasource_service
    assert services.datasource_service is canonical
    assert DATASOURCES_BINDING.extract(services) == (canonical,)
    assert DATASOURCES_BINDING.extract(services)[0] is canonical


def test_carica_binding_uses_runtime_container_singleton_identity():
    """Carica receives the connection_service instance owned by RuntimeServices."""
    from app.runtime import CARICA_BINDING
    from app.runtime.services import build_runtime_services

    services = build_runtime_services()

    args = CARICA_BINDING.extract(services)
    assert args == (services.connection_service,)
    assert args[0] is services.connection_service
