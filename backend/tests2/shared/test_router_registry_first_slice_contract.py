"""Contract test BE-GO-READY-BOOTSTRAP-ENGINE-018-ROUTER-REGISTRY-FIRST-SLICE.

Lock the new typed router-init binding registry:

  - ``app.runtime.router_registry`` exposes a frozen
    :class:`RouterBinding` dataclass and a :func:`bind_router_services`
    dispatcher.
  - The first slice ships exactly one binding: ``ORCHESTRATOR_BINDING``.
    Its ``extract`` returns ``(services.orchestrator,)``; its
    ``init_fn`` ultimately calls
    ``app.core.orchestrator.router.init_orchestrator``.
  - ``bind_router_services`` invokes ``init_fn`` exactly once with the
    extracted positional args. Identity is preserved: the value passed
    to ``init_fn`` IS the container's field, not a freshly-constructed
    double.
  - Source-bound: ``app/main.py`` no longer calls
    ``init_orchestrator(orchestrator)`` directly; the orchestrator
    router does not import from ``app.main``.
  - The registry module is import-cheap: it must not pull in
    FastAPI, settings, DB, or boto3 at import time.

Out of scope (locked by parametrize lists / explicit absence):
  - Registry coverage of the other 5 ``init_*`` calls in ``main.py``
    (those stay procedural for now; follow-up streams own them).
"""

from __future__ import annotations

import ast
from dataclasses import is_dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# ---------------------------------------------------------------------------
# 1. Public API shape
# ---------------------------------------------------------------------------


def test_runtime_module_exposes_registry_surface():
    """Importable surface: ``RouterBinding`` + ``bind_router_services`` +
    the orchestrator binding."""
    from app.runtime import (
        ORCHESTRATOR_BINDING,
        RouterBinding,
        bind_router_services,
    )

    assert is_dataclass(RouterBinding)
    assert RouterBinding.__dataclass_params__.frozen is True
    assert callable(bind_router_services)
    assert isinstance(ORCHESTRATOR_BINDING, RouterBinding)


def test_router_binding_fields_are_typed_and_minimal():
    """The dataclass has exactly 3 fields: name, init_fn, extract.

    Adding a field (e.g. kwargs builder, lifecycle phase) is a
    contract change that must be reflected here. Removing one is a
    regression.
    """
    from dataclasses import fields

    from app.runtime.router_registry import RouterBinding

    field_names = {f.name for f in fields(RouterBinding)}
    assert field_names == {"name", "init_fn", "extract"}


# ---------------------------------------------------------------------------
# 2. Orchestrator binding shape
# ---------------------------------------------------------------------------


def test_orchestrator_binding_name_is_stable():
    """The binding's ``name`` is a stable label. A rename here is a
    contract change (audit logs / test assertions key on it)."""
    from app.runtime import ORCHESTRATOR_BINDING

    assert ORCHESTRATOR_BINDING.name == "orchestrator"


def test_orchestrator_extract_returns_orchestrator_field_only():
    """The extract closure pulls exactly ``services.orchestrator``,
    wrapped in a 1-tuple. Identity preservation is asserted: the
    extracted value IS the container's field, not a copy.
    """
    from app.runtime import ORCHESTRATOR_BINDING

    sentinel_orchestrator = object()
    fake_services = SimpleNamespace(orchestrator=sentinel_orchestrator)

    args = ORCHESTRATOR_BINDING.extract(fake_services)

    assert isinstance(args, tuple)
    assert len(args) == 1
    assert args[0] is sentinel_orchestrator


def test_orchestrator_init_fn_invokes_router_module_init(monkeypatch):
    """The binding's ``init_fn`` resolves to
    ``app.core.orchestrator.router.init_orchestrator`` and forwards
    its positional args verbatim.
    """
    from app.core.orchestrator import router as orchestrator_router_mod
    from app.runtime import ORCHESTRATOR_BINDING

    captured: list = []

    def _fake_init_orchestrator(orch):
        captured.append(orch)

    monkeypatch.setattr(
        orchestrator_router_mod,
        "init_orchestrator",
        _fake_init_orchestrator,
    )

    sentinel = object()
    ORCHESTRATOR_BINDING.init_fn(sentinel)

    assert captured == [sentinel]


# ---------------------------------------------------------------------------
# 3. bind_router_services dispatcher contract
# ---------------------------------------------------------------------------


def test_bind_router_services_calls_init_fn_exactly_once_with_extracted_args():
    """Dispatcher contract: ``init_fn`` is called once, with the tuple
    returned by ``extract``, splatted positionally.
    """
    from app.runtime import RouterBinding, bind_router_services

    init_calls: list = []

    def _fake_init(*args, **kwargs):
        init_calls.append((args, kwargs))

    sentinel = object()
    binding = RouterBinding(
        name="test_only",
        init_fn=_fake_init,
        extract=lambda services: (sentinel,),
    )

    fake_services = SimpleNamespace()
    result = bind_router_services(fake_services, binding)

    assert result is None
    assert len(init_calls) == 1
    args, kwargs = init_calls[0]
    assert args == (sentinel,)
    assert kwargs == {}


def test_bind_router_services_passes_container_owned_instance_identity():
    """Identity invariant: the value passed to ``init_fn`` IS the
    container's field - no reconstruction, no copy.
    """
    from app.runtime import ORCHESTRATOR_BINDING, bind_router_services

    captured: list = []

    real_init = ORCHESTRATOR_BINDING.init_fn

    def _spy_init_fn(*args, **kwargs):
        captured.append(args)

    sentinel_orchestrator = object()
    fake_services = SimpleNamespace(orchestrator=sentinel_orchestrator)

    spy_binding = type(ORCHESTRATOR_BINDING)(
        name=ORCHESTRATOR_BINDING.name,
        init_fn=_spy_init_fn,
        extract=ORCHESTRATOR_BINDING.extract,
    )

    bind_router_services(fake_services, spy_binding)

    assert len(captured) == 1
    (passed,) = captured
    assert passed[0] is sentinel_orchestrator, (
        "init_fn must receive the container's field by identity, "
        "not a freshly-constructed double"
    )

    # Sanity: real init_fn is unaltered (we only swapped on the local
    # spy_binding, the module-level binding is unaffected).
    assert ORCHESTRATOR_BINDING.init_fn is real_init


def test_bind_router_services_attribute_error_on_missing_field_propagates():
    """Fail-fast: a typo on the field name (or a future field rename
    that this binding hasn't followed) surfaces as ``AttributeError``
    at startup, NOT later as a NoneType orchestrator at request-time.
    """
    from app.runtime import RouterBinding, bind_router_services

    binding = RouterBinding(
        name="probe",
        init_fn=lambda *a, **kw: None,
        extract=lambda services: (services.does_not_exist,),
    )

    with pytest.raises(AttributeError):
        bind_router_services(SimpleNamespace(), binding)


# ---------------------------------------------------------------------------
# 4. Source-bound: main.py no longer calls init_orchestrator directly,
#    orchestrator router does not import from app.main.
# ---------------------------------------------------------------------------


_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _read_source(rel_path: str) -> str:
    p = _BACKEND_ROOT / rel_path
    return p.read_text(encoding="utf-8")


def test_main_py_does_not_call_init_orchestrator_directly():
    """``app/main.py`` must route the orchestrator init through the
    registry. A direct ``init_orchestrator(orchestrator)`` call
    bypasses the typed binding and is a regression."""
    src = _read_source("app/main.py")

    # The literal procedural call shape we are migrating away from:
    bad = "init_orchestrator(orchestrator)"
    assert bad not in src, (
        "app/main.py should call the orchestrator init through "
        "bind_router_services(_runtime_services, ORCHESTRATOR_BINDING) "
        "instead of init_orchestrator(orchestrator)"
    )


def test_main_py_imports_the_orchestrator_binding():
    """Positive lock: ``main.py`` imports ``ORCHESTRATOR_BINDING`` and
    invokes ``bind_router_services``. This catches an accidental
    revert.
    """
    src = _read_source("app/main.py")

    assert "ORCHESTRATOR_BINDING" in src, (
        "app/main.py must import ORCHESTRATOR_BINDING from app.runtime"
    )
    assert "bind_router_services(" in src, (
        "app/main.py must invoke bind_router_services()"
    )


def test_main_py_does_not_import_init_orchestrator():
    """The router-side ``init_orchestrator`` symbol must no longer
    appear in ``main.py``'s import list - the registry's lazy thunk
    owns the import now."""
    src = _read_source("app/main.py")

    # Tolerant grep: catches ``init_orchestrator`` in any import form.
    # The string can still appear inside this slice's audit comment;
    # we therefore scan only top-level Import / ImportFrom nodes.
    tree = ast.parse(src)
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)

    assert "init_orchestrator" not in imported_names, (
        "app/main.py must not import init_orchestrator directly; the "
        "registry's lazy thunk imports it on demand"
    )


def test_orchestrator_router_does_not_import_from_main():
    """Architectural lock: routers must not depend on ``app.main``.
    This is the architectural inversion the registry enables: the
    composition root flows from ``main`` -> ``runtime`` -> routers,
    never the reverse.
    """
    src = _read_source("app/core/orchestrator/router.py")
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module != "app.main" and not module.startswith("app.main."), (
                f"app/core/orchestrator/router.py must not import from "
                f"app.main (found: {module})"
            )


# ---------------------------------------------------------------------------
# 5. Module hygiene: the registry must be import-cheap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden_module",
    [
        "fastapi",
        "boto3",
        "psycopg2",
        "redis",
    ],
)
def test_router_registry_module_is_import_cheap(forbidden_module):
    """Importing ``app.runtime.router_registry`` must not pull in
    FastAPI, boto3, psycopg2, or redis at import time. The router's
    own ``init_orchestrator`` stays inside a lazy thunk.
    """
    src = _read_source("app/runtime/router_registry.py")
    tree = ast.parse(src)

    top_level_imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            top_level_imports.add(module.split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top_level_imports.add(alias.name.split(".")[0])

    assert forbidden_module not in top_level_imports, (
        f"app/runtime/router_registry.py must not import "
        f"{forbidden_module} at top level"
    )


def test_router_registry_does_not_import_orchestrator_router_at_top_level():
    """The orchestrator router import must live inside the lazy thunk.
    A top-level import would force the FastAPI router subgraph to load
    whenever any consumer touches ``app.runtime``.
    """
    src = _read_source("app/runtime/router_registry.py")
    tree = ast.parse(src)

    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert "orchestrator.router" not in module, (
                "app.core.orchestrator.router must be imported lazily "
                "inside the binding's init_fn thunk, not at module top"
            )


# ---------------------------------------------------------------------------
# 6. BE-GO-READY-BOOTSTRAP-ENGINE-019: connections binding (2-arg slice)
# ---------------------------------------------------------------------------


def test_runtime_module_exposes_connections_binding():
    """``CONNECTIONS_BINDING`` is part of the public registry surface."""
    from app.runtime import CONNECTIONS_BINDING, RouterBinding

    assert isinstance(CONNECTIONS_BINDING, RouterBinding)


def test_connections_binding_name_is_stable():
    from app.runtime import CONNECTIONS_BINDING

    assert CONNECTIONS_BINDING.name == "connections"


def test_connections_extract_returns_two_typed_fields_in_order():
    """The extract closure pulls ``(connection_service, database_inspector)``
    in that exact positional order. Identity is preserved on both
    fields - the binding does not reconstruct, copy, or reorder.
    """
    from app.runtime import CONNECTIONS_BINDING

    sentinel_conn = object()
    sentinel_inspector = object()
    fake_services = SimpleNamespace(
        connection_service=sentinel_conn,
        database_inspector=sentinel_inspector,
    )

    args = CONNECTIONS_BINDING.extract(fake_services)

    assert isinstance(args, tuple)
    assert len(args) == 2
    assert args[0] is sentinel_conn
    assert args[1] is sentinel_inspector


def test_connections_init_fn_invokes_router_module_init(monkeypatch):
    """The binding's ``init_fn`` resolves to
    ``app.core.connections.router.init_connections`` and forwards its
    positional args verbatim, in order.
    """
    from app.core.connections import router as connections_router_mod
    from app.runtime import CONNECTIONS_BINDING

    captured: list = []

    def _fake_init_connections(connection_service, database_inspector):
        captured.append((connection_service, database_inspector))

    monkeypatch.setattr(
        connections_router_mod,
        "init_connections",
        _fake_init_connections,
    )

    sentinel_conn = object()
    sentinel_inspector = object()
    CONNECTIONS_BINDING.init_fn(sentinel_conn, sentinel_inspector)

    assert captured == [(sentinel_conn, sentinel_inspector)]


def test_bind_connections_passes_both_container_owned_instances_by_identity():
    """Identity invariant for the 2-arg slice: BOTH values reach
    ``init_fn`` by identity, not as fresh constructions or copies.
    """
    from app.runtime import CONNECTIONS_BINDING, bind_router_services

    captured: list = []

    def _spy_init(connection_service, database_inspector):
        captured.append((connection_service, database_inspector))

    sentinel_conn = object()
    sentinel_inspector = object()
    fake_services = SimpleNamespace(
        connection_service=sentinel_conn,
        database_inspector=sentinel_inspector,
    )

    spy_binding = type(CONNECTIONS_BINDING)(
        name=CONNECTIONS_BINDING.name,
        init_fn=_spy_init,
        extract=CONNECTIONS_BINDING.extract,
    )

    bind_router_services(fake_services, spy_binding)

    assert len(captured) == 1
    conn, inspector = captured[0]
    assert conn is sentinel_conn
    assert inspector is sentinel_inspector


def test_bind_connections_attribute_error_on_missing_inspector_field():
    """Fail-fast for the 2-arg extract: a typo on EITHER field surfaces
    as ``AttributeError`` at startup, not as a NoneType inspector
    later at request time.
    """
    from app.runtime import CONNECTIONS_BINDING, bind_router_services

    # Container is missing ``database_inspector``.
    incomplete_services = SimpleNamespace(connection_service=object())

    with pytest.raises(AttributeError):
        bind_router_services(incomplete_services, CONNECTIONS_BINDING)


def test_bind_connections_type_error_on_signature_drift(monkeypatch):
    """Fail-fast on signature drift: if the router renames /
    reorders ``init_connections`` arguments, the dispatcher surfaces
    a ``TypeError`` at startup. Verified by replacing ``init_fn`` with
    a 1-arg function while ``extract`` still returns 2 args.
    """
    from app.runtime import CONNECTIONS_BINDING, bind_router_services

    def _one_arg_init(only_one):
        pass

    drift_binding = type(CONNECTIONS_BINDING)(
        name=CONNECTIONS_BINDING.name,
        init_fn=_one_arg_init,
        extract=CONNECTIONS_BINDING.extract,
    )

    fake_services = SimpleNamespace(
        connection_service=object(),
        database_inspector=object(),
    )

    with pytest.raises(TypeError):
        bind_router_services(fake_services, drift_binding)


def test_main_py_does_not_call_init_connections_directly():
    """``app/main.py`` must route the connections init through the
    registry. A direct ``init_connections(connection_service, database_inspector)``
    call bypasses the typed binding and is a regression."""
    src = _read_source("app/main.py")

    bad = "init_connections(connection_service, database_inspector)"
    assert bad not in src, (
        "app/main.py should call the connections init through "
        "bind_router_services(_runtime_services, CONNECTIONS_BINDING) "
        "instead of init_connections(connection_service, database_inspector)"
    )


def test_main_py_imports_the_connections_binding():
    """Positive lock: ``main.py`` imports ``CONNECTIONS_BINDING``."""
    src = _read_source("app/main.py")

    assert "CONNECTIONS_BINDING" in src, (
        "app/main.py must import CONNECTIONS_BINDING from app.runtime"
    )


def test_main_py_does_not_import_init_connections():
    """The router-side ``init_connections`` symbol must no longer
    appear in ``main.py``'s import list - the registry's lazy thunk
    owns the import now."""
    src = _read_source("app/main.py")

    tree = ast.parse(src)
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)

    assert "init_connections" not in imported_names, (
        "app/main.py must not import init_connections directly; the "
        "registry's lazy thunk imports it on demand"
    )


def test_connections_router_does_not_import_from_main():
    """Architectural lock: connections router must not depend on
    ``app.main``. Composition flows main -> runtime -> router.
    """
    src = _read_source("app/core/connections/router.py")
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module != "app.main" and not module.startswith("app.main."), (
                f"app/core/connections/router.py must not import from "
                f"app.main (found: {module})"
            )


def test_router_registry_does_not_import_connections_router_at_top_level():
    """The connections router import must live inside the lazy thunk
    (same hygiene rule as the orchestrator slice)."""
    src = _read_source("app/runtime/router_registry.py")
    tree = ast.parse(src)

    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert "connections.router" not in module, (
                "app.core.connections.router must be imported lazily "
                "inside the binding's init_fn thunk, not at module top"
            )
