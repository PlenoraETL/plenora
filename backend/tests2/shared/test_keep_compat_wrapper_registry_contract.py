"""Registry contract for active KEEP_COMPAT wrappers.

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
        rel_path="app/services/connection_service.py",
        wrapper="module",
        owner="CONNECTION_ENGINE",
        canonical_target="app.core.connections.service",
        removal_condition=(
            "after legacy connection-service tests import "
            "app.core.connections.service directly"
        ),
    ),
    KeepCompatWrapper(
        rel_path="app/services/file_service.py",
        wrapper="module",
        owner="STORAGE_ARTIFACT_ENGINE",
        canonical_target="app.core.files.service",
        removal_condition=(
            "after legacy file-service tests import app.core.files.service "
            "directly and patch canonical file boundaries"
        ),
    ),
    KeepCompatWrapper(
        rel_path="app/services/file_load_dispatch.py",
        wrapper="module",
        owner="STORAGE_ARTIFACT_ENGINE",
        canonical_target="app.core.files.load_dispatch",
        removal_condition=(
            "after file parser dispatch tests import "
            "app.core.files.load_dispatch directly"
        ),
    ),
    KeepCompatWrapper(
        rel_path="app/services/file_service_deps.py",
        wrapper="module",
        owner="STORAGE_ARTIFACT_ENGINE",
        canonical_target="app.core.files.service_deps",
        removal_condition=(
            "after file dependency tests import "
            "app.core.files.service_deps directly"
        ),
    ),
    KeepCompatWrapper(
        rel_path="app/services/file_storage_dispatch.py",
        wrapper="module",
        owner="STORAGE_ARTIFACT_ENGINE",
        canonical_target="app.core.files.storage_dispatch",
        removal_condition=(
            "after storage dispatch tests import "
            "app.core.files.storage_dispatch directly"
        ),
    ),
    KeepCompatWrapper(
        rel_path="app/services/file_upload.py",
        wrapper="module",
        owner="STORAGE_ARTIFACT_ENGINE",
        canonical_target="app.core.files.upload",
        removal_condition=(
            "after single-file upload tests import app.core.files.upload "
            "directly and patch canonical upload helpers"
        ),
    ),
    KeepCompatWrapper(
        rel_path="app/services/file_source_read.py",
        wrapper="module",
        owner="STORAGE_ARTIFACT_ENGINE",
        canonical_target="app.core.files.source_read",
        removal_condition=(
            "after file-source read tests import "
            "app.core.files.source_read directly"
        ),
    ),
    KeepCompatWrapper(
        rel_path="app/services/file_preview_temp.py",
        wrapper="module",
        owner="STORAGE_ARTIFACT_ENGINE",
        canonical_target="app.core.files.preview_temp",
        removal_condition=(
            "after preview-temp tests import app.core.files.preview_temp "
            "directly and patch canonical preview helpers"
        ),
    ),
    KeepCompatWrapper(
        rel_path="app/services/file_raw_upload.py",
        wrapper="module",
        owner="STORAGE_ARTIFACT_ENGINE",
        canonical_target="app.core.files.raw_upload",
        removal_condition=(
            "after raw upload tests import app.core.files.raw_upload "
            "directly and patch canonical upload helpers"
        ),
    ),
    KeepCompatWrapper(
        rel_path="app/services/file_multi_layer_upload.py",
        wrapper="module",
        owner="STORAGE_ARTIFACT_ENGINE",
        canonical_target="app.core.files.multi_layer_upload",
        removal_condition=(
            "after multi-layer upload tests import "
            "app.core.files.multi_layer_upload directly"
        ),
    ),
    KeepCompatWrapper(
        rel_path="app/services/project_service.py",
        wrapper="module",
        owner="VERSIONING_SNAPSHOT_ENGINE",
        canonical_target="app.core.projects.service",
        removal_condition=(
            "after project-service tests and router patch points import "
            "app.core.projects.service directly"
        ),
    ),
    KeepCompatWrapper(
        rel_path="app/services/project_lineage.py",
        wrapper="module",
        owner="VERSIONING_SNAPSHOT_ENGINE",
        canonical_target="app.core.projects.lineage",
        removal_condition=(
            "after lineage-cycle tests import app.core.projects.lineage "
            "directly"
        ),
    ),
    KeepCompatWrapper(
        rel_path="app/services/schema_inference.py",
        wrapper="module",
        owner="VERSIONING_SNAPSHOT_ENGINE",
        canonical_target="app.core.projects.schema_inference",
        removal_condition=(
            "after schema-inference tests import "
            "app.core.projects.schema_inference directly"
        ),
    ),
    KeepCompatWrapper(
        rel_path="app/services/task_service.py",
        wrapper="module",
        owner="ORCHESTRATION_ENGINE",
        canonical_target="app.core.orchestrator.task_service",
        removal_condition=(
            "after task router and async-permission tests import "
            "app.core.orchestrator.task_service directly"
        ),
    ),
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
    KeepCompatWrapper(
        rel_path="app/core/auth/auth.py",
        wrapper="_check_rate_limit_in_memory",
        owner="AUTH_SECURITY_ENGINE",
        canonical_target=(
            "app.core.auth.rate_limit_fallback."
            "RateLimitFallbackStore.check"
        ),
        removal_condition=(
            "after auth tests patch RateLimitFallbackStore.check directly "
            "instead of the auth facade helper"
        ),
    ),
)

_REMOVED_ZERO_CONSUMER_WRAPPERS: frozenset[str] = frozenset(
    {
        "app/services/log_archive.py",
        "app/services/project_domain.py",
        "app/services/project_repository.py",
    }
)

_MODULE_COMPAT_ALIASES: tuple[tuple[str, str], ...] = (
    ("app.services.connection_service", "app.core.connections.service"),
    ("app.services.file_load_dispatch", "app.core.files.load_dispatch"),
    ("app.services.file_storage_dispatch", "app.core.files.storage_dispatch"),
    ("app.services.file_upload", "app.core.files.upload"),
    ("app.services.file_raw_upload", "app.core.files.raw_upload"),
    ("app.services.file_multi_layer_upload", "app.core.files.multi_layer_upload"),
    ("app.services.file_preview_temp", "app.core.files.preview_temp"),
    ("app.services.file_source_read", "app.core.files.source_read"),
    ("app.services.file_service", "app.core.files.service"),
    ("app.services.project_service", "app.core.projects.service"),
)


def _source(rel_path: str) -> str:
    return (_BACKEND_ROOT / rel_path).read_text(encoding="utf-8")


def _parse(rel_path: str) -> ast.AST:
    return ast.parse(_source(rel_path))


def _module_docstring(rel_path: str) -> str:
    return ast.get_docstring(_parse(rel_path)) or ""


def _legacy_service_wrappers() -> set[str]:
    wrappers: set[str] = set()
    for path in (_APP_ROOT / "services").glob("*.py"):
        if path.name == "__init__.py":
            continue
        rel_path = path.relative_to(_BACKEND_ROOT).as_posix()
        if "KEEP_COMPAT" in _module_docstring(rel_path):
            wrappers.add(rel_path)
    return wrappers


def _module_name(rel_path: str) -> str:
    return rel_path.removesuffix(".py").replace("/", ".")


def _imported_compat_modules(path: Path, removed_modules: set[str]) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in removed_modules:
            hits.add(node.module)
        elif isinstance(node, ast.Import):
            hits.update(alias.name for alias in node.names if alias.name in removed_modules)
    return hits


def _references_module(path: Path, module_name: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
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


def test_app_services_keep_compat_wrappers_are_registered():
    registered = {
        wrapper.rel_path
        for wrapper in _KEEP_COMPAT_WRAPPERS
        if wrapper.rel_path.startswith("app/services/")
    }
    active = _legacy_service_wrappers()

    assert active == registered, (
        "`app.services` KEEP_COMPAT wrappers must be listed in "
        "`_KEEP_COMPAT_WRAPPERS` with owner and canonical target.\n"
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
        "`app.services` KEEP_COMPAT wrappers without test/runtime consumers "
        "must be deleted and moved to `_REMOVED_ZERO_CONSUMER_WRAPPERS`.\n"
        f"Stale wrappers: {sorted(stale_wrappers)}"
    )


def test_removed_zero_consumer_wrappers_stay_removed():
    removed_modules = {
        _module_name(rel_path)
        for rel_path in _REMOVED_ZERO_CONSUMER_WRAPPERS
    }

    for rel_path in _REMOVED_ZERO_CONSUMER_WRAPPERS:
        assert not (_BACKEND_ROOT / rel_path).exists(), (
            f"{rel_path} was removed as a zero-consumer compatibility wrapper."
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
