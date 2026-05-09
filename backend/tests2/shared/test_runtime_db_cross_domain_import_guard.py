"""Runtime modules must not import raw core DB leaves.

This is intentionally scoped to the Wave 2 cleaned/runtime surfaces. It is
not a whole-repo guard: the goal is to prevent backsliding in selected modules
while residual runtime call sites are made explicit with removal conditions.
Same-domain raw DB imports are only checked for explicitly named cleaned
surfaces where a boundary already exists or is being introduced.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.architecture]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_ROOT = _BACKEND_ROOT / "app"
_CORE_ROOT = _APP_ROOT / "core"

_TARGET_FILES: tuple[str, ...] = (
    "app/core/artifacts/resolver.py",
    "app/core/auth/owner_dependencies.py",
    "app/core/manipola/executor.py",
    "app/core/orchestrator/task_service.py",
    "app/core/projects/router.py",
    "app/core/projects/schema_inference.py",
    "app/core/results/retention.py",
    "app/core/results/store.py",
    "app/services/task_service.py",
    "app/utils/debug_payload_builder.py",
    "app/utils/result_store.py",
    "workers/tasks/pipeline_task.py",
    "workers/tasks/carica_task.py",
)
_TARGET_PACKAGES: tuple[str, ...] = (
    "app/core/arricchisci",
    "app/core/carica",
    "app/core/connections",
    "app/core/datasources",
    "app/core/imdf",
    "app/core/scheduler",
)

_ALLOWED_SEVERITIES = frozenset({"HIGH", "MEDIUM", "LOW"})
_ALLOWED_REASON_PREFIXES = (
    "KEEP_COMPAT_WITH_REMOVAL_CONDITION:",
    "BLOCKER:",
)
_SAME_DOMAIN_RAW_DB_TARGET_FILES = frozenset(
    {
        "app/core/projects/router.py",
        "app/core/projects/schema_inference.py",
    }
)


@dataclass(frozen=True)
class RuntimeDbImport:
    src_path: str
    src_domain: str | None
    imported_domain: str
    imported_module: str
    imported_name: str
    line: int


@dataclass(frozen=True)
class AllowedRuntimeDbImport:
    src_path: str
    imported_module: str
    imported_name: str
    severity: str
    reason: str


@dataclass(frozen=True)
class RemovedRuntimeDbImport:
    src_path: str
    imported_module: str
    imported_name: str
    reason: str


_ALLOWED_RUNTIME_DB_IMPORTS: tuple[AllowedRuntimeDbImport, ...] = ()
_REMOVED_RUNTIME_DB_IMPORTS: tuple[RemovedRuntimeDbImport, ...] = (
    RemovedRuntimeDbImport(
        "app/core/arricchisci/api_connection_rest_file.py",
        "app.core.files.db",
        "<module>",
        "REMOVED: REST-file file_source path materialization now consumes the files-owned source_loading boundary",
    ),
    RemovedRuntimeDbImport(
        "app/core/auth/owner_dependencies.py",
        "app.core.files.db",
        "get_file_source",
        "REMOVED: file ownership checks now consume the files-owned ownership context boundary",
    ),
    RemovedRuntimeDbImport(
        "app/core/auth/owner_dependencies.py",
        "app.core.scheduler.db",
        "get_schedule",
        "REMOVED: schedule ownership checks now consume the scheduler-owned ownership context boundary",
    ),
    RemovedRuntimeDbImport(
        "app/core/results/retention.py",
        "app.core.projects.db",
        "get_project_retention_n",
        "REMOVED: results retention now consumes project runtime-context retention resolution",
    ),
    RemovedRuntimeDbImport(
        "app/core/results/store.py",
        "app.core.projects.db",
        "get_project",
        "REMOVED: result store group resolution now consumes project runtime-context group resolution",
    ),
    RemovedRuntimeDbImport(
        "workers/tasks/carica_task.py",
        "app.core.projects",
        "db",
        "REMOVED: CARICA upstream pre-cache now consumes project runtime-context group resolution",
    ),
    RemovedRuntimeDbImport(
        "app/core/projects/service.py",
        "app.core.results",
        "db",
        "REMOVED: project result helpers now consume the results-owned store boundary",
    ),
    RemovedRuntimeDbImport(
        "app/core/imdf/project_access.py",
        "app.core.projects",
        "db",
        "REMOVED: IMDF project access now consumes the projects-owned access_context boundary",
    ),
    RemovedRuntimeDbImport(
        "workers/tasks/pipeline_task.py",
        "app.core.intermediate",
        "db",
        "REMOVED: pipeline task run lifecycle now consumes the intermediate-owned run_lifecycle boundary",
    ),
)


def _rel(path: Path) -> str:
    return path.relative_to(_BACKEND_ROOT).as_posix()


def _target_paths() -> list[Path]:
    paths = {_BACKEND_ROOT / rel_path for rel_path in _TARGET_FILES}
    for rel_package in _TARGET_PACKAGES:
        paths.update((_BACKEND_ROOT / rel_package).rglob("*.py"))
    return sorted(path for path in paths if path.is_file())


def _src_domain(path: Path) -> str | None:
    try:
        rel_parts = path.relative_to(_CORE_ROOT).parts
    except ValueError:
        return None
    return rel_parts[0] if rel_parts else None


def _db_import_domain(module: str, imported_name: str | None = None) -> str | None:
    parts = module.split(".")
    if len(parts) == 4 and parts[:2] == ["app", "core"] and parts[3] == "db":
        return parts[2]
    if (
        imported_name == "db"
        and len(parts) == 3
        and parts[:2] == ["app", "core"]
    ):
        return parts[2]
    return None


def _edge_key(
    edge: RuntimeDbImport | AllowedRuntimeDbImport | RemovedRuntimeDbImport,
) -> tuple[str, str, str]:
    return (edge.src_path, edge.imported_module, edge.imported_name)


def _iter_raw_cross_domain_db_imports(path: Path) -> list[RuntimeDbImport]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    rel_path = _rel(path)
    src_domain = _src_domain(path)
    include_same_domain_raw_db = rel_path in _SAME_DOMAIN_RAW_DB_TARGET_FILES
    edges: list[RuntimeDbImport] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported_domain = _db_import_domain(node.module, alias.name)
                if imported_domain is None:
                    continue
                if imported_domain == src_domain and not include_same_domain_raw_db:
                    continue
                edges.append(
                    RuntimeDbImport(
                        src_path=rel_path,
                        src_domain=src_domain,
                        imported_domain=imported_domain,
                        imported_module=node.module,
                        imported_name=alias.name,
                        line=node.lineno,
                    )
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_domain = _db_import_domain(alias.name)
                if imported_domain is None:
                    continue
                if imported_domain == src_domain and not include_same_domain_raw_db:
                    continue
                edges.append(
                    RuntimeDbImport(
                        src_path=rel_path,
                        src_domain=src_domain,
                        imported_domain=imported_domain,
                        imported_module=alias.name,
                        imported_name="<module>",
                        line=node.lineno,
                    )
                )

    return edges


def _current_raw_cross_domain_db_imports() -> list[RuntimeDbImport]:
    edges: list[RuntimeDbImport] = []
    for path in _target_paths():
        edges.extend(_iter_raw_cross_domain_db_imports(path))
    return sorted(edges, key=lambda edge: (*_edge_key(edge), edge.line))


def _removed_import_source_edges() -> list[RuntimeDbImport]:
    paths = {
        _BACKEND_ROOT / edge.src_path
        for edge in _REMOVED_RUNTIME_DB_IMPORTS
    }
    edges: list[RuntimeDbImport] = []
    for path in sorted(paths):
        if path.is_file():
            edges.extend(_iter_raw_cross_domain_db_imports(path))
    return edges


def test_selected_runtime_modules_do_not_add_raw_cross_domain_db_imports() -> None:
    allowed_by_key = {
        _edge_key(edge): edge for edge in _ALLOWED_RUNTIME_DB_IMPORTS
    }
    current_edges = _current_raw_cross_domain_db_imports()
    violations = [
        edge for edge in current_edges if _edge_key(edge) not in allowed_by_key
    ]

    assert not violations, (
        "Selected runtime modules must not import raw cross-domain "
        "app.core.<domain>.db leaves. Route through a domain service/boundary "
        "or add a narrow allowlist entry with severity and removal condition. "
        f"Violations: {violations}"
    )

    seen_keys = {_edge_key(edge) for edge in current_edges}
    stale_allowed = [
        edge for edge in _ALLOWED_RUNTIME_DB_IMPORTS if _edge_key(edge) not in seen_keys
    ]
    assert not stale_allowed, (
        "Runtime cross-domain DB allowlist drift detected. Remove stale "
        f"allowed imports: {stale_allowed}"
    )


def test_runtime_db_import_allowlist_has_severity_and_removal_condition() -> None:
    keys = [_edge_key(edge) for edge in _ALLOWED_RUNTIME_DB_IMPORTS]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert not duplicates
    assert not (
        {_edge_key(edge) for edge in _ALLOWED_RUNTIME_DB_IMPORTS}
        & {_edge_key(edge) for edge in _REMOVED_RUNTIME_DB_IMPORTS}
    )

    for edge in _ALLOWED_RUNTIME_DB_IMPORTS:
        assert edge.severity in _ALLOWED_SEVERITIES, (
            f"{edge.src_path} -> {edge.imported_module}: invalid severity "
            f"{edge.severity!r}; use HIGH, MEDIUM, or LOW."
        )
        assert edge.reason.startswith(_ALLOWED_REASON_PREFIXES), (
            f"{edge.src_path} -> {edge.imported_module}: allowlist reason "
            "must start with KEEP_COMPAT_WITH_REMOVAL_CONDITION: or BLOCKER: "
            "and state the removal path."
        )

    for edge in _REMOVED_RUNTIME_DB_IMPORTS:
        assert edge.reason.startswith("REMOVED:"), edge


def test_removed_runtime_db_import_tombstones_stay_absent() -> None:
    current_by_key = {
        _edge_key(edge): edge
        for edge in (
            _current_raw_cross_domain_db_imports()
            + _removed_import_source_edges()
        )
    }
    reintroduced = [
        edge
        for edge in _REMOVED_RUNTIME_DB_IMPORTS
        if _edge_key(edge) in current_by_key
    ]

    assert not reintroduced, (
        "Removed runtime raw DB imports must stay tombstoned. Reintroduced "
        f"imports: {reintroduced}"
    )


def test_cleaned_runtime_surfaces_stay_raw_db_import_free() -> None:
    raw_db_free_files = (
        "app/core/artifacts/resolver.py",
        "app/core/auth/owner_dependencies.py",
        "app/core/orchestrator/task_service.py",
        "app/core/projects/router.py",
        "app/core/projects/schema_inference.py",
        "app/core/results/retention.py",
        "app/core/results/store.py",
        "app/services/task_service.py",
        "app/utils/debug_payload_builder.py",
        "app/utils/result_store.py",
    )
    offenders = {
        edge
        for edge in _current_raw_cross_domain_db_imports()
        if edge.src_path in raw_db_free_files
    }

    assert not offenders, (
        "Cleaned runtime surfaces must keep DB access behind their named "
        f"boundaries. Offenders: {sorted(offenders, key=_edge_key)}"
    )
