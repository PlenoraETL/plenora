"""Registry contract for active KEEP_COMPAT wrappers and deleted tombstones.

Wrappers are allowed while callers migrate, but each one needs an
explicit owner, canonical target, and removal condition so the
compatibility surface stays temporary and measurable.
"""

from __future__ import annotations

from dataclasses import dataclass
import ast
import importlib
import importlib.util
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_ROOT = _BACKEND_ROOT / "app"
_PROD_ROOTS = (_BACKEND_ROOT / "app", _BACKEND_ROOT / "workers")


@dataclass(frozen=True)
class KeepCompatWrapper:
    rel_path: str
    wrapper: str
    owner: str
    canonical_target: str
    removal_condition: str

    @property
    def canonical_module(self) -> str:
        parts = self.canonical_target.split(".")
        for index in range(len(parts), 0, -1):
            candidate = ".".join(parts[:index])
            try:
                found = importlib.util.find_spec(candidate) is not None
            except (AttributeError, ModuleNotFoundError):
                found = False
            if found:
                return candidate
        return self.canonical_target


_KEEP_COMPAT_WRAPPERS: tuple[KeepCompatWrapper, ...] = (
    KeepCompatWrapper(
        rel_path="app/core/arricchisci/api_connector.py",
        wrapper="ApiConnectionService.execute_enrichment",
        owner="TRANSFORMATION_ENGINE",
        canonical_target=(
            "app.core.arricchisci.canvas_orchestrator."
            "execute_canvas_enrichment"
        ),
        removal_condition=(
            "after ApiConnectionService callers invoke "
            "execute_canvas_enrichment through the canvas orchestrator"
        ),
    ),
    KeepCompatWrapper(
        rel_path="app/core/arricchisci/api_connector.py",
        wrapper="ApiConnectionService._init_execution_context",
        owner="TRANSFORMATION_ENGINE",
        canonical_target=(
            "app.core.arricchisci.canvas_orchestrator."
            "init_execution_context"
        ),
        removal_condition=(
            "after ApiConnectionService callers construct execution context "
            "through the canvas orchestrator"
        ),
    ),
    KeepCompatWrapper(
        rel_path="app/core/arricchisci/execution_context.py",
        wrapper="ArricchisciExecutionContext",
        owner="TRANSFORMATION_ENGINE",
        canonical_target="app.shared.execution.context.CanvasExecutionContext",
        removal_condition=(
            "after arricchisci callers and tests import "
            "CanvasExecutionContext directly"
        ),
    ),
)

_REMOVED_ZERO_CONSUMER_WRAPPERS: frozenset[str] = frozenset(
    {
        "app/services/log_archive.py",
        "app/services/file_load_dispatch.py",
        "app/services/file_multi_layer_upload.py",
        "app/services/file_preview_temp.py",
        "app/services/file_raw_upload.py",
        "app/services/file_service.py",
        "app/services/file_service_deps.py",
        "app/services/file_source_read.py",
        "app/services/file_storage_dispatch.py",
        "app/services/file_upload.py",
        "app/services/connection_service.py",
        "app/services/orphan_cleanup_service.py",
        "app/services/project_domain.py",
        "app/services/project_lineage.py",
        "app/services/project_repository.py",
        "app/services/project_service.py",
        "app/services/schema_inference.py",
        "app/services/task_service.py",
    }
)

_MODULE_COMPAT_ALIASES: tuple[tuple[str, str], ...] = ()
_PLAIN_KEEP_COMPAT_PRODUCTION_ALLOWLIST: frozenset[str] = frozenset()
_LEGACY_DB_FACADE_CONSUMER_ALLOWLIST = {
    "app.db.tickets_db_service": frozenset(
        {
            "app/core/admin_system_status/tickets_metrics.py",
            "app/core/tickets/db.py",
        }
    ),
    "app.db.log_runs_db_service": frozenset(
        {
            "app/core/orchestrator/log_cleanup.py",
        }
    ),
}


def _source(rel_path: str) -> str:
    return (_BACKEND_ROOT / rel_path).read_text(encoding="utf-8")


def _parse(rel_path: str) -> ast.AST:
    return ast.parse(_source(rel_path))


def _parse_path(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8-sig"))


def _module_docstring(rel_path: str) -> str:
    return ast.get_docstring(_parse(rel_path)) or ""


def _iter_prod_python() -> list[Path]:
    paths: list[Path] = []
    for root in _PROD_ROOTS:
        if root.exists():
            paths.extend(root.rglob("*.py"))
    return sorted(paths)


def _rel(path: Path) -> str:
    return path.relative_to(_BACKEND_ROOT).as_posix()


def _plain_keep_compat_production_markers() -> set[str]:
    markers: set[str] = set()
    for path in _iter_prod_python():
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            stripped = line.strip()
            if "KEEP_COMPAT" not in stripped:
                continue
            if "KEEP_COMPAT_WITH_REMOVAL_CONDITION" in stripped:
                continue
            markers.add(f"{_rel(path)}::{stripped}")
    return markers


def _import_edges(tree: ast.AST) -> set[str]:
    edges: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            edges.update(alias.name for alias in node.names)
            continue
        if isinstance(node, ast.ImportFrom) and node.module:
            edges.add(node.module)
            edges.update(f"{node.module}.{alias.name}" for alias in node.names)
    return edges


def _class_method_names(rel_path: str, class_name: str) -> set[str]:
    tree = _parse(rel_path)
    return {
        child.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == class_name
        for child in node.body
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _active_service_compat_wrappers() -> set[str]:
    wrappers: set[str] = set()
    services_root = _APP_ROOT / "services"
    if not services_root.exists():
        return wrappers
    for path in services_root.glob("*.py"):
        if path.name == "__init__.py":
            continue
        rel_path = path.relative_to(_BACKEND_ROOT).as_posix()
        if "KEEP_COMPAT" in _module_docstring(rel_path):
            wrappers.add(rel_path)
    return wrappers


def _module_name(rel_path: str) -> str:
    return rel_path.removesuffix(".py").replace("/", ".")


def _imported_compat_modules(path: Path, removed_modules: set[str]) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    hits: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in removed_modules:
            hits.add(node.module)
        elif isinstance(node, ast.Import):
            hits.update(alias.name for alias in node.names if alias.name in removed_modules)
    return hits


def _references_module(path: Path, module_name: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    parent, leaf = module_name.rsplit(".", 1)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == module_name for alias in node.names):
                return True
            continue
        if isinstance(node, ast.ImportFrom):
            if node.module == module_name:
                return True
            if node.module == parent and any(alias.name == leaf for alias in node.names):
                return True
            continue
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and module_name in node.value
        ):
            return True
    return False


def _consumer_references(module_name: str) -> list[str]:
    references: list[str] = []
    registry_path = Path(__file__).relative_to(_BACKEND_ROOT).as_posix()
    for root in (_BACKEND_ROOT / "app", _BACKEND_ROOT / "tests2"):
        for path in root.rglob("*.py"):
            rel_path = path.relative_to(_BACKEND_ROOT).as_posix()
            if rel_path == registry_path:
                continue
            if _references_module(path, module_name):
                references.append(rel_path)
    return sorted(references)


def test_keep_compat_wrappers_have_owner_and_canonical_target():
    missing_contract = [
        wrapper
        for wrapper in _KEEP_COMPAT_WRAPPERS
        if not wrapper.owner
        or not wrapper.canonical_target
        or not wrapper.removal_condition
    ]
    assert not missing_contract


def test_keep_compat_wrappers_have_specific_removal_conditions():
    vague = [
        wrapper
        for wrapper in _KEEP_COMPAT_WRAPPERS
        if "after" not in wrapper.removal_condition.lower()
    ]
    assert not vague


@pytest.mark.parametrize("wrapper", _KEEP_COMPAT_WRAPPERS)
def test_keep_compat_wrapper_sources_point_to_canonical_target(
    wrapper: KeepCompatWrapper,
):
    source = _source(wrapper.rel_path)
    canonical_module = wrapper.canonical_module

    assert "KEEP_COMPAT" in source, (
        f"{wrapper.rel_path}:{wrapper.wrapper} is registered as a "
        "compatibility wrapper, but the source no longer marks it "
        "KEEP_COMPAT."
    )
    assert canonical_module in source, (
        f"{wrapper.rel_path}:{wrapper.wrapper} must document or import "
        f"its canonical target module `{canonical_module}`."
    )
    assert importlib.util.find_spec(canonical_module) is not None, (
        f"{wrapper.rel_path}:{wrapper.wrapper} points at missing "
        f"canonical module `{canonical_module}`."
    )


def test_app_services_compat_wrappers_stay_tombstoned_or_registered():
    registered = {
        wrapper.rel_path
        for wrapper in _KEEP_COMPAT_WRAPPERS
        if wrapper.rel_path.startswith("app/services/")
    }
    active = _active_service_compat_wrappers()

    assert active == registered, (
        "`app.services` compatibility wrappers are tombstoned. Any "
        "reintroduced wrapper must be explicitly registered with owner, "
        "canonical target, and removal condition.\n"
        f"Unregistered: {sorted(active - registered)}\n"
        f"Stale registry rows: {sorted(registered - active)}"
    )


def test_app_services_keep_compat_wrappers_have_live_test_or_runtime_consumers():
    stale_wrappers: dict[str, list[str]] = {}
    for wrapper in _KEEP_COMPAT_WRAPPERS:
        if not wrapper.rel_path.startswith("app/services/"):
            continue
        module_name = _module_name(wrapper.rel_path)
        consumers = _consumer_references(module_name)
        if not consumers:
            stale_wrappers[wrapper.rel_path] = consumers

    assert not stale_wrappers, (
        "`app.services` compatibility wrappers without test/runtime "
        "consumers must stay deleted and listed as tombstones.\n"
        f"Stale wrappers: {sorted(stale_wrappers)}"
    )


def test_final_state_has_no_unregistered_plain_keep_compat_markers():
    current = _plain_keep_compat_production_markers()

    assert current == _PLAIN_KEEP_COMPAT_PRODUCTION_ALLOWLIST, (
        "Final compatibility state allows only explicitly registered plain "
        "KEEP_COMPAT production markers. Prefer "
        "`KEEP_COMPAT_WITH_REMOVAL_CONDITION: remove after ...` for live "
        "compatibility debt.\n"
        f"Unexpected: {sorted(current - _PLAIN_KEEP_COMPAT_PRODUCTION_ALLOWLIST)}\n"
        f"Stale allowlist rows: {sorted(_PLAIN_KEEP_COMPAT_PRODUCTION_ALLOWLIST - current)}"
    )


def test_final_state_has_no_package_getattr_legacy_bridges():
    package_getattrs = [
        _rel(path)
        for path in _iter_prod_python()
        if path.name == "__init__.py"
        and any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "__getattr__"
            for node in ast.walk(_parse_path(path))
        )
    ]

    assert not package_getattrs, (
        "Package-level __getattr__ bridges are legacy import compatibility "
        f"surfaces and must stay removed. Violations: {package_getattrs}"
    )


def test_final_state_task_and_schema_services_have_no_getattr_bridge():
    service_classes = (
        ("app/core/orchestrator/task_service.py", "TaskService"),
        ("app/core/projects/schema_inference.py", "SchemaInferenceService"),
    )
    violations = [
        f"{rel_path}::{class_name}.__getattr__"
        for rel_path, class_name in service_classes
        if "__getattr__" in _class_method_names(rel_path, class_name)
    ]

    assert not violations, (
        "TaskService and SchemaInferenceService must expose explicit typed "
        f"methods, not __getattr__ compatibility bridges. Violations: {violations}"
    )


def test_final_state_legacy_app_services_and_db_facade_consumers_are_closed():
    violations: list[tuple[str, str]] = []
    for path in _iter_prod_python():
        rel_path = _rel(path)
        edges = _import_edges(_parse_path(path))
        for edge in edges:
            if edge == "app.services" or edge.startswith("app.services."):
                violations.append((rel_path, edge))
        for facade, allowed_paths in _LEGACY_DB_FACADE_CONSUMER_ALLOWLIST.items():
            if any(edge == facade or edge.startswith(f"{facade}.") for edge in edges):
                if rel_path not in allowed_paths:
                    violations.append((rel_path, facade))

    assert not violations, (
        "Legacy app.services and ticket/log_runs DB facade consumers must stay "
        "closed outside their explicit domain-boundary allowlist. "
        f"Violations: {violations}"
    )


def test_removed_zero_consumer_wrappers_stay_removed():
    removed_modules = {
        _module_name(rel_path)
        for rel_path in _REMOVED_ZERO_CONSUMER_WRAPPERS
    }

    for rel_path in _REMOVED_ZERO_CONSUMER_WRAPPERS:
        assert not (_BACKEND_ROOT / rel_path).exists(), (
            f"{rel_path} is a tombstoned zero-consumer compatibility wrapper."
        )

    offenders: dict[str, list[str]] = {}
    for root in (_BACKEND_ROOT / "app", _BACKEND_ROOT / "tests2"):
        for path in root.rglob("*.py"):
            rel_path = path.relative_to(_BACKEND_ROOT).as_posix()
            if rel_path == Path(__file__).relative_to(_BACKEND_ROOT).as_posix():
                continue
            hits = _imported_compat_modules(path, removed_modules)
            if hits:
                offenders[rel_path] = sorted(hits)

    assert offenders == {}


@pytest.mark.parametrize(("legacy_module", "canonical_module"), _MODULE_COMPAT_ALIASES)
def test_module_compat_wrappers_alias_canonical_modules(
    legacy_module: str,
    canonical_module: str,
):
    legacy = importlib.import_module(legacy_module)
    canonical = importlib.import_module(canonical_module)

    assert legacy is canonical
