"""
Architecture ledger for legacy import containers.

Batch 081 Worker 6: keep `app.services` and `app.db.config`
production import debt measurable by domain. Batch 084 Worker 1
tombstoned `app.db.db_service`: it remains in this ledger only as a
forbidden zero-baseline container. Batch 087 Worker 3 added explicit
zero-baseline rows for the closed `app.db.config_db_service` facade and
the retained `app.db.results_db_service` compatibility tombstone. These
baselines are intentionally exact: new import edges fail, and removed
edges fail until the baseline is trimmed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_ROOT = _BACKEND_ROOT / "app" / "db" / "config"
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
        "projects": frozenset(),
        "connections": frozenset(),
        "files_storage": frozenset(),
        "tasks_orchestration": frozenset(),
        "auth_datasources_other": frozenset(),
    },
    "app.db.db_service": {
        "projects": frozenset(),
        "connections": frozenset(),
        "files_storage": frozenset(),
        "tasks_orchestration": frozenset(),
        "auth_datasources_other": frozenset(),
    },
    "app.db.config_db_service": {
        "projects": frozenset(),
        "connections": frozenset(),
        "files_storage": frozenset(),
        "tasks_orchestration": frozenset(),
        "auth_datasources_other": frozenset(),
    },
    "app.db.results_db_service": {
        "projects": frozenset(),
        "connections": frozenset(),
        "files_storage": frozenset(),
        "tasks_orchestration": frozenset(),
        "auth_datasources_other": frozenset(),
    },
    "app.db.config": {
        "projects": frozenset({
            "app/core/projects/service.py -> app.db.config.comparison_keys",
        }),
        "connections": frozenset({
            "app/core/arricchisci/api_connection_management.py -> app.db.config.api_connections",
            "app/db/config_db_service.py -> app.db.config.api_connections",
        }),
        "files_storage": frozenset(),
        "tasks_orchestration": frozenset(),
        "auth_datasources_other": frozenset({
            "app/db/config_db_service.py -> app.db.config.comparison_keys",
        }),
    },
}

_ALLOWED_LEDGER_STATUSES = frozenset(
    {
        "DONE",
        "REMOVED",
        "KEEP_PUBLIC_COMPAT",
        "KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        "BLOCKER",
    }
)

_PROJECT_CONFIG_IMPORT_BLOCKERS: dict[str, str] = {}
_DELETED_CONFIG_LEAVES = frozenset(
    {
        "connections.py",
        "datasources.py",
        "file_sources.py",
        "groups.py",
        "projects.py",
        "safe_set.py",
        "schedules.py",
        "users.py",
    }
)


def _rel(path: Path) -> str:
    return path.relative_to(_BACKEND_ROOT).as_posix()


def _parse(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8-sig"))
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
    For tombstoned containers such as `app.db.db_service`, any edge is
    forbidden debt. Removed edges also fail so the baseline gets
    trimmed and the count ratchets downward.
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
    """Exact count snapshot for Batch 081 residual reporting."""
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


def test_project_config_leaf_production_imports_are_explicit_blockers():
    """Project persistence must not regain runtime imports of the legacy leaf.

    This is intentionally project-only for BE Batch 098. Test-only imports of
    the explicit compatibility module are outside this production runtime scan.
    """
    found = _scan_legacy_imports("app.db.config")["projects"]
    project_leaf_edges = {
        edge for edge in found if edge.endswith(" -> app.core.projects.db")
    }

    assert set(_PROJECT_CONFIG_IMPORT_BLOCKERS) == project_leaf_edges
    assert set(_PROJECT_CONFIG_IMPORT_BLOCKERS.values()) <= _ALLOWED_LEDGER_STATUSES
    assert not project_leaf_edges


def test_deleted_config_leaves_stay_absent():
    reintroduced = sorted(
        leaf for leaf in _DELETED_CONFIG_LEAVES if (_CONFIG_ROOT / leaf).exists()
    )

    assert not reintroduced, (
        "Deleted app.db.config persistence leaves were reintroduced. "
        f"Use the domain-owned DB boundary instead: {reintroduced}"
    )


def test_config_package_has_no_magic_bridge_modules():
    """Config tombstones/compat leaves must stay explicit named exports only."""
    scanned = [*_CONFIG_ROOT.glob("*.py"), _BACKEND_ROOT / "app" / "db" / "config_db_service.py"]
    violations: list[str] = []

    for path in scanned:
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__getattr__":
                violations.append(f"{_rel(path)} defines __getattr__")
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Subscript) and ast.unparse(target.value) == "sys.modules":
                        violations.append(f"{_rel(path)} assigns sys.modules")

    assert not violations
