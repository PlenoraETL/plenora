"""Registry contract for retained `app.db` compatibility facades.

Batch 105 Wave 2: these modules are compatibility-only export surfaces.
They may stay while broad tests and legacy harnesses still import them, but
production runtime code must not grow new imports or new facade-owned exports.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import importlib
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.shared]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_PROD_ROOTS = (_BACKEND_ROOT / "app", _BACKEND_ROOT / "workers")


@dataclass(frozen=True)
class FacadeExport:
    facade_module: str
    facade_name: str
    canonical_module: str
    canonical_name: str | None = None

    @property
    def rel_path(self) -> str:
        return self.facade_module.replace(".", "/") + ".py"

    @property
    def resolved_canonical_name(self) -> str:
        return self.canonical_name or self.facade_name


_CONFIG_EXPORTS: tuple[FacadeExport, ...] = (
    FacadeExport("app.db.config_db_service", "fetchone", "app.db.pg_connection"),
    FacadeExport("app.db.config_db_service", "fetchall", "app.db.pg_connection"),
    FacadeExport("app.db.config_db_service", "execute", "app.db.pg_connection"),
    FacadeExport("app.db.config_db_service", "executemany", "app.db.pg_connection"),
    FacadeExport("app.db.config_db_service", "transaction", "app.db.pg_connection"),
    FacadeExport(
        "app.db.config_db_service", "get_connection", "app.db.pg_connection"
    ),
    FacadeExport(
        "app.db.config_db_service", "put_connection", "app.db.pg_connection"
    ),
    FacadeExport(
        "app.db.config_db_service", "close_connection", "app.db.pg_connection"
    ),
    FacadeExport("app.db.config_db_service", "get_group", "app.core.auth.db"),
    FacadeExport("app.db.config_db_service", "list_groups", "app.core.auth.db"),
    FacadeExport("app.db.config_db_service", "create_group", "app.core.auth.db"),
    FacadeExport(
        "app.db.config_db_service", "get_group_summary", "app.core.auth.db"
    ),
    FacadeExport("app.db.config_db_service", "delete_group", "app.core.auth.db"),
    FacadeExport(
        "app.db.config_db_service",
        "delete_group_cascade",
        "app.core.auth.group_cleanup",
    ),
    FacadeExport("app.db.config_db_service", "get_user", "app.core.auth.db"),
    FacadeExport(
        "app.db.config_db_service", "get_user_by_username", "app.core.auth.db"
    ),
    FacadeExport("app.db.config_db_service", "list_users", "app.core.auth.db"),
    FacadeExport("app.db.config_db_service", "create_user", "app.core.auth.db"),
    FacadeExport("app.db.config_db_service", "update_user", "app.core.auth.db"),
    FacadeExport(
        "app.db.config_db_service", "update_user_password", "app.core.auth.db"
    ),
    FacadeExport("app.db.config_db_service", "delete_user", "app.core.auth.db"),
    FacadeExport("app.db.config_db_service", "get_project", "app.core.projects.db"),
    FacadeExport(
        "app.db.config_db_service", "list_projects", "app.core.projects.db"
    ),
    FacadeExport(
        "app.db.config_db_service", "create_project", "app.core.projects.db"
    ),
    FacadeExport(
        "app.db.config_db_service", "update_project", "app.core.projects.db"
    ),
    FacadeExport(
        "app.db.config_db_service", "delete_project", "app.core.projects.db"
    ),
    FacadeExport(
        "app.db.config_db_service",
        "get_project_retention_n",
        "app.core.projects.db",
    ),
    FacadeExport(
        "app.db.config_db_service",
        "update_project_canvas_key_atomic",
        "app.core.projects.db",
    ),
    FacadeExport(
        "app.db.config_db_service",
        "delete_comparison_keys",
        "app.core.projects.db",
    ),
    FacadeExport(
        "app.db.config_db_service",
        "get_connection_config",
        "app.core.connections.db",
    ),
    FacadeExport(
        "app.db.config_db_service",
        "list_connections",
        "app.core.connections.db",
        "list_connection_configs",
    ),
    FacadeExport(
        "app.db.config_db_service",
        "create_connection",
        "app.core.connections.db",
    ),
    FacadeExport(
        "app.db.config_db_service",
        "update_connection",
        "app.core.connections.db",
    ),
    FacadeExport(
        "app.db.config_db_service",
        "delete_connection",
        "app.core.connections.db",
    ),
    FacadeExport("app.db.config_db_service", "FileSourceRow", "app.core.files.db"),
    FacadeExport("app.db.config_db_service", "get_file_source", "app.core.files.db"),
    FacadeExport(
        "app.db.config_db_service", "list_file_sources", "app.core.files.db"
    ),
    FacadeExport(
        "app.db.config_db_service", "save_file_source", "app.core.files.db"
    ),
    FacadeExport(
        "app.db.config_db_service", "save_file_sources_batch", "app.core.files.db"
    ),
    FacadeExport(
        "app.db.config_db_service",
        "update_file_source_metadata",
        "app.core.files.db",
    ),
    FacadeExport(
        "app.db.config_db_service", "delete_file_source", "app.core.files.db"
    ),
    FacadeExport(
        "app.db.config_db_service", "DataSourceRow", "app.core.datasources.db"
    ),
    FacadeExport(
        "app.db.config_db_service", "get_datasource", "app.core.datasources.db"
    ),
    FacadeExport(
        "app.db.config_db_service", "list_datasources", "app.core.datasources.db"
    ),
    FacadeExport(
        "app.db.config_db_service", "save_datasource", "app.core.datasources.db"
    ),
    FacadeExport(
        "app.db.config_db_service", "update_datasource", "app.core.datasources.db"
    ),
    FacadeExport(
        "app.db.config_db_service", "delete_datasource", "app.core.datasources.db"
    ),
    FacadeExport(
        "app.db.config_db_service", "get_api_connection", "app.core.arricchisci.db"
    ),
    FacadeExport(
        "app.db.config_db_service",
        "list_api_connections",
        "app.core.arricchisci.db",
    ),
    FacadeExport(
        "app.db.config_db_service",
        "save_api_connection",
        "app.core.arricchisci.db",
    ),
    FacadeExport(
        "app.db.config_db_service",
        "create_api_connection",
        "app.core.arricchisci.db",
    ),
    FacadeExport(
        "app.db.config_db_service",
        "update_api_connection",
        "app.core.arricchisci.db",
    ),
    FacadeExport(
        "app.db.config_db_service",
        "delete_api_connection",
        "app.core.arricchisci.db",
    ),
    FacadeExport("app.db.config_db_service", "get_schedule", "app.core.scheduler.db"),
    FacadeExport(
        "app.db.config_db_service", "list_schedules", "app.core.scheduler.db"
    ),
    FacadeExport(
        "app.db.config_db_service", "save_schedule", "app.core.scheduler.db"
    ),
    FacadeExport(
        "app.db.config_db_service", "update_schedule", "app.core.scheduler.db"
    ),
    FacadeExport(
        "app.db.config_db_service", "delete_schedule", "app.core.scheduler.db"
    ),
)

_RESULTS_EXPORTS: tuple[FacadeExport, ...] = (
    FacadeExport("app.db.results_db_service", "delete_result", "app.core.results.cleanup"),
    FacadeExport(
        "app.db.results_db_service",
        "delete_results_for_project",
        "app.core.results.cleanup",
    ),
    FacadeExport("app.db.results_db_service", "get_latest_result", "app.core.results.db"),
    FacadeExport("app.db.results_db_service", "get_result", "app.core.results.db"),
    FacadeExport("app.db.results_db_service", "list_results", "app.core.results.db"),
    FacadeExport("app.db.results_db_service", "save_result", "app.core.results.db"),
    FacadeExport("app.db.results_db_service", "close_connection", "app.db.pg_connection"),
    FacadeExport("app.db.results_db_service", "transaction", "app.db.pg_connection"),
)

_FACADE_EXPORTS = _CONFIG_EXPORTS + _RESULTS_EXPORTS
_FACADE_MODULES = frozenset(export.facade_module for export in _FACADE_EXPORTS)
_FACADE_ALLOWED_FUNCTIONS = {
    "app.db.config_db_service": frozenset({"init_db"}),
    "app.db.results_db_service": frozenset(
        {"init_db", "get_result_data", "_s3_download_bytes"}
    ),
}


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8-sig"))


def _rel(path: Path) -> str:
    return path.relative_to(_BACKEND_ROOT).as_posix()


def _source_declared_app_exports(facade_module: str) -> set[str]:
    path = _BACKEND_ROOT / facade_module.replace(".", "/")
    tree = _parse(path.with_suffix(".py"))
    exported: set[str] = set()

    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("app.core") or node.module == "app.db.pg_connection":
                exported.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if not (alias.asname or alias.name).startswith("_")
                )
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            exported.add(node.name)

    return exported


def _imports_facade(tree: ast.AST, facade_module: str) -> bool:
    parent, leaf = facade_module.rsplit(".", 1)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == facade_module for alias in node.names):
                return True
            continue
        if isinstance(node, ast.ImportFrom):
            if node.module == facade_module:
                return True
            if node.module == parent and any(alias.name == leaf for alias in node.names):
                return True
    return False


def test_app_db_compat_facades_have_explicit_export_registries():
    """Any app-owned top-level export must be listed in this registry."""
    for facade_module in sorted(_FACADE_MODULES):
        registered = {
            export.facade_name
            for export in _FACADE_EXPORTS
            if export.facade_module == facade_module
        } | _FACADE_ALLOWED_FUNCTIONS[facade_module]
        declared = _source_declared_app_exports(facade_module)

        assert declared == registered, (
            f"`{facade_module}` app-owned exports changed. These files are "
            "compatibility facades only; add canonical exports deliberately "
            "to the registry or move the new API to a domain-owned module.\n"
            f"Unregistered: {sorted(declared - registered)}\n"
            f"Stale registry rows: {sorted(registered - declared)}"
        )


@pytest.mark.parametrize("export", _FACADE_EXPORTS)
def test_app_db_compat_facade_exports_are_identity_reexports(export: FacadeExport):
    if export.canonical_module == "app.db.pg_connection":
        pytest.skip("pg_connection test stubs mutate module attributes after import")

    facade = importlib.import_module(export.facade_module)
    canonical = importlib.import_module(export.canonical_module)

    assert getattr(facade, export.facade_name) is getattr(
        canonical, export.resolved_canonical_name
    )


@pytest.mark.parametrize("facade_module", sorted(_FACADE_MODULES))
def test_app_db_compat_facades_have_no_production_runtime_imports(
    facade_module: str,
):
    """Runtime code must use domain-owned modules, not the compat facades."""
    violations: list[str] = []
    for root in _PROD_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if path == _BACKEND_ROOT / facade_module.replace(".", "/"):
                continue
            try:
                tree = _parse(path)
            except SyntaxError:
                continue
            if _imports_facade(tree, facade_module):
                violations.append(_rel(path))

    assert not violations, (
        f"Production runtime imports `{facade_module}`. Import the canonical "
        "domain-owned module instead; the facade is retained only for "
        "compatibility tests and legacy harnesses.\nViolations:\n  - "
        + "\n  - ".join(sorted(violations))
    )
