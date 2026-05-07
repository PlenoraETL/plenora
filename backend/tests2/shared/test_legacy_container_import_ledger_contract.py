"""
Architecture ledger for remaining legacy import containers.

Batch 080 Worker 6: keep `app.services`, `app.db.db_service`, and
`app.db.config` production import debt measurable by domain. These
baselines are intentionally exact: new import edges fail, and removed
edges fail until the baseline is trimmed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_PROD_ROOTS = (_BACKEND_ROOT / "app", _BACKEND_ROOT / "workers")
_DOMAINS = (
    "projects",
    "connections",
    "files_storage",
    "tasks_orchestration",
    "auth_datasources_other",
)


_LEGACY_IMPORT_BASELINES: dict[str, dict[str, frozenset[str]]] = {
    "app.services": {
        "projects": frozenset({
            "app/core/manipola/executor.py -> app.services.project_lineage",
            "app/core/projects/router.py -> app.services.orphan_cleanup_service",
            "app/core/projects/router.py -> app.services.project_lineage",
            "app/core/projects/router.py -> app.services.schema_inference",
            "app/core/projects/service.py -> app.services.project_lineage",
            "app/shared/execution/canvas_columns.py -> app.services.schema_inference",
        }),
        "connections": frozenset(),
        "files_storage": frozenset({
            "app/core/files/service.py -> app.services.file_load_dispatch",
            "app/core/files/service.py -> app.services.file_multi_layer_upload",
            "app/core/files/service.py -> app.services.file_preview_temp",
            "app/core/files/service.py -> app.services.file_raw_upload",
            "app/core/files/service.py -> app.services.file_service_deps",
            "app/core/files/service.py -> app.services.file_source_read",
            "app/core/files/service.py -> app.services.file_storage_dispatch",
            "app/core/files/service.py -> app.services.file_upload",
            "app/services/file_multi_layer_upload.py -> app.services.file_storage_dispatch",
            "app/services/file_multi_layer_upload.py -> app.services.file_upload",
            "app/services/file_preview_temp.py -> app.services.file_load_dispatch",
            "app/services/file_raw_upload.py -> app.services.file_storage_dispatch",
            "app/services/file_upload.py -> app.services.file_storage_dispatch",
        }),
        "tasks_orchestration": frozenset({
            "workers/beat_scheduler.py -> app.services.log_archive",
        }),
        "auth_datasources_other": frozenset(),
    },
    "app.db.db_service": {
        "projects": frozenset({
            "app/core/arricchisci/canvas_orchestrator.py -> app.db.db_service",
        }),
        "connections": frozenset(),
        "files_storage": frozenset(),
        "tasks_orchestration": frozenset({
            "workers/beat_scheduler.py -> app.db.db_service",
            "workers/tasks/carica_task.py -> app.db.db_service",
            "workers/tasks/schedule_task.py -> app.db.db_service",
        }),
        "auth_datasources_other": frozenset({
            "app/bootstrap.py -> app.db.db_service",
            "app/core/arricchisci/api_connector.py -> app.db.db_service",
            "app/core/auth/shares_router.py -> app.db.db_service",
        }),
    },
    "app.db.config": {
        "projects": frozenset({
            "app/core/arricchisci/execution_context.py -> app.db.config.projects",
            "app/core/auth/owner_dependencies.py -> app.db.config.projects",
            "app/core/connections/router.py -> app.db.config.projects",
            "app/core/datasources/router.py -> app.db.config.projects",
            "app/core/imdf/router.py -> app.db.config.projects",
            "app/core/intermediate/db.py -> app.db.config.projects",
            "app/core/manipola/executor.py -> app.db.config.projects",
            "app/core/projects/db.py -> app.db.config.projects",
            "app/core/projects/repository.py -> app.db.config.projects",
            "app/core/projects/service.py -> app.db.config.comparison_keys",
            "app/core/projects/service.py -> app.db.config.groups",
            "app/core/projects/service.py -> app.db.config.projects",
            "app/db/config_db_service.py -> app.db.config.projects",
            "app/services/schema_inference.py -> app.db.config.projects",
            "app/utils/result_store.py -> app.db.config.projects",
            "workers/tasks/pipeline_task.py -> app.db.config.projects",
        }),
        "connections": frozenset({
            "app/core/arricchisci/api_connection_management.py -> app.db.config.api_connections",
            "app/core/arricchisci/api_connection_rest_file.py -> app.db.config.file_sources",
            "app/core/connections/db.py -> app.db.config.connections",
            "app/db/config_db_service.py -> app.db.config.api_connections",
            "app/db/config_db_service.py -> app.db.config.connections",
        }),
        "files_storage": frozenset({
            "app/core/auth/owner_dependencies.py -> app.db.config.file_sources",
            "app/core/files/router.py -> app.db.config.file_sources",
            "app/core/tickets/router.py -> app.db.config.file_sources",
            "app/core/files/service.py -> app.db.config.file_sources",
            "app/db/config_db_service.py -> app.db.config.file_sources",
            "app/services/file_multi_layer_upload.py -> app.db.config.file_sources",
            "app/shared/authz_helpers.py -> app.db.config.file_sources",
        }),
        "tasks_orchestration": frozenset({
            "app/core/auth/owner_dependencies.py -> app.db.config.schedules",
            "app/core/scheduler/execution_manager.py -> app.db.config.schedules",
            "app/core/scheduler/router.py -> app.db.config.schedules",
            "app/core/scheduler/schedule_service.py -> app.db.config.schedules",
            "app/db/config_db_service.py -> app.db.config.schedules",
        }),
        "auth_datasources_other": frozenset({
            "app/core/auth/auth.py -> app.db.config.users",
            "app/core/auth/dependencies.py -> app.db.config.users",
            "app/core/auth/groups_router.py -> app.db.config.groups",
            "app/core/auth/ownership.py -> app.db.config.users",
            "app/core/auth/users_router.py -> app.db.config.groups",
            "app/core/auth/users_router.py -> app.db.config.users",
            "app/core/datasources/service.py -> app.db.config.datasources",
            "app/db/config_db_service.py -> app.db.config.comparison_keys",
            "app/db/config_db_service.py -> app.db.config.datasources",
            "app/db/config_db_service.py -> app.db.config.groups",
            "app/db/config_db_service.py -> app.db.config.users",
            "app/shared/async_user_recheck.py -> app.db.config.users",
        }),
    },
}


def _rel(path: Path) -> str:
    return path.relative_to(_BACKEND_ROOT).as_posix()


def _parse(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def _iter_prod_python() -> list[Path]:
    paths: list[Path] = []
    for root in _PROD_ROOTS:
        if root.exists():
            paths.extend(root.rglob("*.py"))
    return sorted(paths)


def _imported_modules(tree: ast.AST) -> frozenset[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
            continue
        if isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
            if module in {"app.services", "app.db.config", "app.db"}:
                modules.update(f"{module}.{alias.name}" for alias in node.names)
            else:
                modules.add(module)
    return frozenset(modules)


def _domain_for(rel_path: str, import_module: str) -> str:
    subject = f"{rel_path} {import_module}"
    if any(token in subject for token in ("project", "canvas", "lineage")):
        return "projects"
    if any(token in subject for token in ("connection", "api_connection")):
        return "connections"
    if any(
        token in subject
        for token in ("file", "storage", "artifact", "result", "blob", "imdf", "gpkg", "shp")
    ):
        return "files_storage"
    if any(
        token in subject
        for token in ("task", "worker", "scheduler", "schedule", "orchestrator", "celery", "beat")
    ):
        return "tasks_orchestration"
    return "auth_datasources_other"


def _empty_domain_map() -> dict[str, set[str]]:
    return {domain: set() for domain in _DOMAINS}


def _scan_legacy_imports(prefix: str) -> dict[str, set[str]]:
    found = _empty_domain_map()
    for path in _iter_prod_python():
        tree = _parse(path)
        if tree is None:
            continue
        rel_path = _rel(path)
        for module in sorted(_imported_modules(tree)):
            if module == prefix or module.startswith(f"{prefix}."):
                domain = _domain_for(rel_path, module)
                found[domain].add(f"{rel_path} -> {module}")
    return found


@pytest.mark.parametrize("prefix", tuple(_LEGACY_IMPORT_BASELINES))
def test_legacy_container_import_ledger_by_domain(prefix: str):
    """Ratcheting ledger for legacy production import containers.

    Existing import edges remain listed to make the debt measurable.
    New edges fail as new debt. Removed edges also fail so the
    baseline gets trimmed and the count ratchets downward.
    """
    expected = _LEGACY_IMPORT_BASELINES[prefix]
    found = _scan_legacy_imports(prefix)

    extra: dict[str, list[str]] = {}
    missing: dict[str, list[str]] = {}
    for domain in _DOMAINS:
        expected_edges = expected[domain]
        found_edges = found[domain]
        if found_edges - expected_edges:
            extra[domain] = sorted(found_edges - expected_edges)
        if expected_edges - found_edges:
            missing[domain] = sorted(expected_edges - found_edges)

    assert not extra, (
        f"New production imports from `{prefix}` detected. Migrate to a "
        "domain-owned module or update the ledger with an explicit residual.\n"
        f"New edges by domain: {extra}"
    )
    assert not missing, (
        f"`{prefix}` import ledger has stale entries. Trim the baseline so "
        f"the ratchet reflects removed debt.\nStale edges by domain: {missing}"
    )


def test_legacy_container_import_ledger_current_counts_are_locked():
    """Exact count snapshot for Batch 080 residual reporting."""
    counts = {
        prefix: {
            domain: len(edges)
            for domain, edges in _scan_legacy_imports(prefix).items()
        }
        for prefix in _LEGACY_IMPORT_BASELINES
    }
    expected_counts = {
        prefix: {
            domain: len(edges)
            for domain, edges in domains.items()
        }
        for prefix, domains in _LEGACY_IMPORT_BASELINES.items()
    }
    assert counts == expected_counts
