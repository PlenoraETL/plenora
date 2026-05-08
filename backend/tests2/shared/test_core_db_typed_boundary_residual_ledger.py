"""1.0 residual ledger for typed core/DB boundary seams.

This is intentionally narrow: it locks the Batch 094 DB/core rows plus the
BE095-BE097 Wave A-C typed-boundary closures and compatibility wrappers.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_ROOT = _BACKEND_ROOT / "app"

_ALLOWED_STATUSES = frozenset(
    {
        "DONE",
        "REMOVED",
        "KEEP_PUBLIC_COMPAT",
        "KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        "BLOCKER",
    }
)


@dataclass(frozen=True)
class BoundaryEntry:
    path: str
    symbol: str
    status: str
    reason: str


_LEDGER: tuple[BoundaryEntry, ...] = (
    BoundaryEntry(
        "app/core/projects/db.py",
        "ProjectRow",
        "DONE",
        "project list DB rows are immutable domain-owned dataclasses",
    ),
    BoundaryEntry(
        "app/core/projects/db.py",
        "ProjectDetailRow",
        "DONE",
        "project detail DB rows are immutable domain-owned dataclasses",
    ),
    BoundaryEntry(
        "app/core/projects/db.py",
        "ProjectDependencyRow",
        "DONE",
        "connection-delete dependency checks use domain-owned typed project rows",
    ),
    BoundaryEntry(
        "app/core/projects/db.py",
        "ProjectCanvasCheckRow",
        "DONE",
        "datasource cascade checks use domain-owned typed project rows",
    ),
    BoundaryEntry(
        "app/core/projects/db.py",
        "get_project",
        "KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        "remove after project API and worker callers consume get_project_row and serialize at their public edges",
    ),
    BoundaryEntry(
        "app/core/projects/db.py",
        "create_project_row",
        "DONE",
        "project create post-write hydration returns ProjectDetailRow from the domain DB boundary",
    ),
    BoundaryEntry(
        "app/core/projects/db.py",
        "create_project",
        "KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        "remove after project create callers consume create_project_row and serialize at their public edges",
    ),
    BoundaryEntry(
        "app/core/projects/db.py",
        "update_project_row",
        "DONE",
        "project update post-write hydration returns ProjectDetailRow from the domain DB boundary",
    ),
    BoundaryEntry(
        "app/core/projects/db.py",
        "update_project",
        "KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        "remove after project update callers consume update_project_row and serialize at their public edges",
    ),
    BoundaryEntry(
        "app/core/projects/db.py",
        "list_projects_using_connection",
        "KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        "remove after connection-delete checks consume list_project_dependency_rows_using_connection",
    ),
    BoundaryEntry(
        "app/core/projects/db.py",
        "list_projects_with_canvas_for_datasource_check",
        "KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        "remove after datasource cascade checks consume list_project_canvas_check_rows",
    ),
    BoundaryEntry(
        "app/db/config/projects.py",
        "module",
        "REMOVED",
        "project persistence compat leaf removed after tests migrated to app.core.projects.db",
    ),
    BoundaryEntry(
        "app/core/projects/db.py",
        "delete_comparison_keys",
        "DONE",
        "project-owned persistence boundary cleans comparison-key rows for project delete cascade",
    ),
    BoundaryEntry(
        "app/db/config/comparison_keys.py",
        "module",
        "REMOVED",
        "comparison-key SQL moved to app.core.projects.db and config leaf removed",
    ),
    BoundaryEntry(
        "app/core/results/db.py",
        "ResultMetadataRow",
        "DONE",
        "result metadata reads use immutable row contracts",
    ),
    BoundaryEntry(
        "app/core/results/db.py",
        "get_result",
        "KEEP_PUBLIC_COMPAT",
        "legacy metadata wrapper returns dict at public edge",
    ),
    BoundaryEntry(
        "app/core/results/db.py",
        "get_latest_result",
        "KEEP_PUBLIC_COMPAT",
        "legacy metadata wrapper returns dict at public edge",
    ),
    BoundaryEntry(
        "app/core/results/db.py",
        "list_results",
        "KEEP_PUBLIC_COMPAT",
        "legacy metadata wrapper returns dict list at public edge",
    ),
    BoundaryEntry(
        "app/core/results/db.py",
        "save_result",
        "KEEP_PUBLIC_COMPAT",
        "public save helper still returns legacy metadata dict",
    ),
    BoundaryEntry(
        "app/core/results/db.py",
        "save_result_row",
        "DONE",
        "result metadata writes hydrate ResultMetadataRow at the core DB boundary",
    ),
    BoundaryEntry(
        "app/core/scheduler/db.py",
        "ScheduleRow",
        "DONE",
        "schedule DB reads and post-write reads use immutable scheduler-owned row contracts",
    ),
    BoundaryEntry(
        "app/core/scheduler/db.py",
        "get_schedule",
        "KEEP_PUBLIC_COMPAT",
        "legacy schedule dict wrapper remains at the scheduler DB boundary for public compatibility callers",
    ),
    BoundaryEntry(
        "app/core/scheduler/db.py",
        "list_schedules",
        "KEEP_PUBLIC_COMPAT",
        "legacy schedule dict list wrapper remains at the scheduler DB boundary for public compatibility callers",
    ),
    BoundaryEntry(
        "app/db/config/schedules.py",
        "module",
        "REMOVED",
        "schedule persistence config leaf removed after imports migrated to app.core.scheduler.db",
    ),
    BoundaryEntry(
        "app/core/connections/db.py",
        "ConnectionConfigRow",
        "DONE",
        "connection domain service consumes typed config rows",
    ),
    BoundaryEntry(
        "app/core/connections/db.py",
        "ArcGISConnectionRow",
        "DONE",
        "ArcGIS provider resolution consumes typed connection rows",
    ),
    BoundaryEntry(
        "app/core/connections/db.py",
        "ConnectionConfigRow.to_legacy_dict",
        "KEEP_PUBLIC_COMPAT",
        "public dict conversion stays explicit for external callers that still need it",
    ),
    BoundaryEntry(
        "app/core/connections/db.py",
        "create_connection",
        "DONE",
        "connection writes hydrate ConnectionConfigRow at the core DB boundary",
    ),
    BoundaryEntry(
        "app/core/connections/db.py",
        "update_connection",
        "DONE",
        "connection updates hydrate ConnectionConfigRow at the core DB boundary",
    ),
    BoundaryEntry(
        "app/core/connections/db.py",
        "list_connection_configs",
        "DONE",
        "connection lists return ConnectionConfigRow objects at the core DB boundary",
    ),
    BoundaryEntry(
        "app/core/connections/db.py",
        "delete_connection",
        "DONE",
        "connection deletes execute SQL inside the domain-owned DB boundary",
    ),
    BoundaryEntry(
        "app/core/connections/arcgis_connection_resolver.py",
        "resolve_arcgis_connection",
        "DONE",
        "ArcGIS connection resolution returns typed ArcGISConnectionRow data",
    ),
    BoundaryEntry(
        "app/core/connections/arcgis_client.py",
        "ArcGISClient",
        "KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        "remove after concrete-client tests and monkeypatch paths stop importing app.core.features.arcgis_client",
    ),
    BoundaryEntry(
        "app/core/connections/arcgis_provider_factory.py",
        "create_arcgis_provider",
        "DONE",
        "provider construction is exposed through the neutral connection factory",
    ),
    BoundaryEntry(
        "app/db/config/connections.py",
        "module",
        "REMOVED",
        "connection persistence config leaf removed after config_db_service exports moved to app.core.connections.db",
    ),
    BoundaryEntry(
        "app/core/arricchisci/db.py",
        "ApiConnectionRow",
        "DONE",
        "API connection DB rows are immutable arricchisci-owned dataclasses",
    ),
    BoundaryEntry(
        "app/core/arricchisci/db.py",
        "ApiConnectionRow.to_legacy_dict",
        "KEEP_PUBLIC_COMPAT",
        "legacy API connection dict conversion stays explicit while router/service callers preserve public shapes",
    ),
    BoundaryEntry(
        "app/db/config/api_connections.py",
        "module",
        "REMOVED",
        "API connection persistence config leaf removed after config_db_service exports moved to app.core.arricchisci.db",
    ),
    BoundaryEntry(
        "app/core/datasources/db.py",
        "DataSourceRow",
        "DONE",
        "datasource persistence rows are immutable domain-owned dataclasses",
    ),
    BoundaryEntry(
        "app/core/datasources/db.py",
        "DataSourceRow.to_legacy_dict",
        "KEEP_PUBLIC_COMPAT",
        "public datasource compatibility conversion stays explicit while service internals still consume dict shape",
    ),
    BoundaryEntry(
        "app/core/datasources/db.py",
        "get_datasource",
        "DONE",
        "datasource reads return DataSourceRow objects at the domain DB boundary",
    ),
    BoundaryEntry(
        "app/core/datasources/db.py",
        "list_datasources",
        "DONE",
        "datasource lists return DataSourceRow objects at the domain DB boundary",
    ),
    BoundaryEntry(
        "app/core/datasources/db.py",
        "save_datasource",
        "DONE",
        "datasource saves hydrate DataSourceRow at the domain DB boundary",
    ),
    BoundaryEntry(
        "app/db/config/datasources.py",
        "module",
        "REMOVED",
        "datasource persistence config leaf removed after config_db_service exports moved to app.core.datasources.db",
    ),
    BoundaryEntry(
        "app/db/config/users.py",
        "module",
        "REMOVED",
        "user persistence config leaf removed after imports migrated to app.core.auth.db",
    ),
    BoundaryEntry(
        "app/db/config/groups.py",
        "module",
        "REMOVED",
        "group persistence config leaf removed after imports migrated to app.core.auth.db",
    ),
    BoundaryEntry(
        "app/db/config/file_sources.py",
        "module",
        "REMOVED",
        "file-source persistence config leaf removed after imports migrated to app.core.files.db",
    ),
    BoundaryEntry(
        "app/core/orchestrator/task_service.py",
        "TaskResultField",
        "DONE",
        "Celery result metadata is preserved as typed JSON-compatible public fields",
    ),
    BoundaryEntry(
        "app/core/orchestrator/task_service.py",
        "TaskResultPayload",
        "DONE",
        "Celery result metadata uses explicit typed fields instead of a broad payload mapping",
    ),
    BoundaryEntry(
        "app/core/orchestrator/task_service.py",
        "ActiveTaskInfo",
        "DONE",
        "active/reserved Celery task rows are typed inside TaskService",
    ),
    BoundaryEntry(
        "app/core/orchestrator/task_service.py",
        "TaskInfo.to_dict",
        "REMOVED",
        "task info serialization is owned by app.core.orchestrator.router._task_info_to_dict",
    ),
    BoundaryEntry(
        "app/core/orchestrator/task_service.py",
        "TaskService.get_task_result",
        "DONE",
        "completed-task Celery results are normalized into TaskResultPayload",
    ),
    BoundaryEntry(
        "app/core/orchestrator/router.py",
        "_task_info_to_dict",
        "KEEP_PUBLIC_COMPAT",
        "task status dict serialization is kept at the router/API boundary",
    ),
    BoundaryEntry(
        "app/core/features/__init__.py",
        "ArcGISClient",
        "REMOVED",
        "package-level concrete ArcGIS client re-export had zero consumers",
    ),
    BoundaryEntry(
        "app/core/features/__init__.py",
        "create_client",
        "REMOVED",
        "package-level ArcGIS factory re-export had zero consumers",
    ),
    BoundaryEntry(
        "app/core/features/arcgis_connection_adapter.py",
        "module",
        "REMOVED",
        "Features-owned ArcGIS connection adapter was replaced by app.core.connections.arcgis_connection_resolver",
    ),
    BoundaryEntry(
        "workers/tasks/carica_task.py",
        "_execute_arcgis_write",
        "KEEP_PUBLIC_COMPAT",
        "Carica worker delegates ArcGIS writes to the domain boundary but still returns public Celery result dict shape",
    ),
    BoundaryEntry(
        "app/core/features/arcgis_feature_operations.py",
        "ArcGISFeatureOperations",
        "KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        "provider operation methods still return public ArcGIS dict shapes until response models cover writes/reads",
    ),
    BoundaryEntry(
        "app/core/features/features_service.py",
        "FeaturesService.write_features_from_df",
        "KEEP_PUBLIC_COMPAT",
        "public FeaturesService write seam delegates to ArcGISFeatureOperations and preserves API dict shape",
    ),
    BoundaryEntry(
        "app/core/features/features_service.py",
        "FeaturesService.create_service_from_df",
        "KEEP_PUBLIC_COMPAT",
        "public FeaturesService create-service seam delegates to ArcGISFeatureOperations and preserves API dict shape",
    ),
    BoundaryEntry(
        "app/core/eseguibili/db.py",
        "EseguibileRow",
        "DONE",
        "eseguibili detail DB reads and writes return frozen row models",
    ),
    BoundaryEntry(
        "app/core/eseguibili/db.py",
        "EseguibileSummaryRow",
        "DONE",
        "eseguibili list DB reads return frozen summary row models",
    ),
    BoundaryEntry(
        "app/core/eseguibili/db.py",
        "EseguibileSourceRefRow",
        "DONE",
        "project delete dependency checks consume typed eseguibile source refs",
    ),
    BoundaryEntry(
        "app/core/eseguibili/db.py",
        "EseguibileRow.to_legacy_dict",
        "KEEP_PUBLIC_COMPAT",
        "router/service compatibility keeps explicit dict serialization for the public eseguibili shape",
    ),
    BoundaryEntry(
        "app/core/eseguibili/service.py",
        "create_from_project",
        "DONE",
        "create_from_project returns an EseguibileRow and router code owns public dict serialization",
    ),
    BoundaryEntry(
        "app/core/eseguibili/service.py",
        "analyze_file_upload",
        "KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        "remove after upload analysis returns a typed payload model and the router serializes the response",
    ),
    BoundaryEntry(
        "app/core/eseguibili/service.py",
        "execute",
        "KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        "remove after eseguibile execution returns a typed task-launch payload and the router serializes the response",
    ),
    BoundaryEntry(
        "app/core/admin_system_status/models.py",
        "AdminStatusBlock",
        "DONE",
        "admin status probes return typed status blocks",
    ),
    BoundaryEntry(
        "app/core/admin_system_status/models.py",
        "SystemStatusPayload",
        "DONE",
        "admin status aggregate returns a typed payload model before router serialization",
    ),
    BoundaryEntry(
        "app/core/admin_system_status/router.py",
        "get_system_status",
        "KEEP_PUBLIC_COMPAT",
        "admin status endpoint preserves the public dashboard JSON shape",
    ),
)


def _tree(rel_path: str) -> ast.Module:
    return ast.parse((_BACKEND_ROOT / rel_path).read_text(encoding="utf-8-sig"))


def _rel(path: Path) -> str:
    return path.relative_to(_BACKEND_ROOT).as_posix()


def _classes(tree: ast.AST) -> set[str]:
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}


def _defined_symbols(tree: ast.AST) -> set[str]:
    symbols: set[str] = set()
    scope: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            qualified = ".".join([*scope, node.name])
            symbols.add(qualified)
            scope.append(node.name)
            self.generic_visit(node)
            scope.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            qualified = ".".join([*scope, node.name])
            symbols.add(qualified)
            scope.append(node.name)
            self.generic_visit(node)
            scope.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            qualified = ".".join([*scope, node.name])
            symbols.add(qualified)
            scope.append(node.name)
            self.generic_visit(node)
            scope.pop()

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if scope:
                return
            symbols.update(alias.asname or alias.name for alias in node.names)

        def visit_Assign(self, node: ast.Assign) -> None:
            if scope:
                return
            for target in node.targets:
                if isinstance(target, ast.Name):
                    symbols.add(target.id)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if scope:
                return
            if isinstance(node.target, ast.Name):
                symbols.add(node.target.id)

    Visitor().visit(tree)
    return symbols


def _symbol_exists(entry: BoundaryEntry) -> bool:
    path = _BACKEND_ROOT / entry.path
    if not path.exists():
        return False
    if entry.symbol == "module":
        return True
    return entry.symbol in _defined_symbols(_tree(entry.path))


def _function_returns(tree: ast.AST) -> dict[str, str]:
    returns: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                returns[node.name] = ast.unparse(node.returns)
    return returns


def _top_level_function_returns(tree: ast.Module) -> dict[str, str]:
    returns: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
            returns[node.name] = ast.unparse(node.returns)
    return returns


def _decorator_names(node: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Name):
            names.add(decorator.id)
        elif isinstance(decorator, ast.Attribute):
            names.add(decorator.attr)
        elif isinstance(decorator, ast.Call):
            func = decorator.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _dataclass_frozen_flags(tree: ast.AST) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if "dataclass" not in _decorator_names(node):
            continue
        frozen = False
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call):
                frozen = any(
                    keyword.arg == "frozen" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True
                    for keyword in decorator.keywords
                )
        flags[node.name] = frozen
    return flags


def _pydantic_forbid_classes(tree: ast.AST) -> set[str]:
    classes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {ast.unparse(base) for base in node.bases}
        if "BaseModel" not in bases:
            continue
        for item in node.body:
            if not isinstance(item, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) and target.id == "model_config" for target in item.targets):
                continue
            if "extra='forbid'" in ast.unparse(item.value) or 'extra="forbid"' in ast.unparse(item.value):
                classes.add(node.name)
    return classes


def _public_dict_returns(rel_path: str) -> set[str]:
    returns = _top_level_function_returns(_tree(rel_path))
    found: set[str] = set()
    for name, annotation in returns.items():
        if name.startswith("_"):
            continue
        if "Dict" in annotation or "dict" in annotation:
            found.add(name)
    return found


def test_core_db_boundary_ledger_uses_only_1_0_statuses():
    statuses = {entry.status for entry in _LEDGER}
    assert statuses <= _ALLOWED_STATUSES
    assert "MIGRATE_NEXT" not in statuses
    assert "KEEP_COMPAT" not in statuses


def test_final_1_0_ledger_has_no_duplicate_entries():
    keys = [(entry.path, entry.symbol) for entry in _LEDGER]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert not duplicates


def test_final_1_0_ledger_has_no_stale_live_entries():
    stale_live = [
        entry
        for entry in _LEDGER
        if entry.status != "REMOVED" and not _symbol_exists(entry)
    ]
    stale_removed = [
        entry
        for entry in _LEDGER
        if entry.status == "REMOVED" and _symbol_exists(entry)
    ]

    assert not stale_live, (
        "Non-removed ledger entries must point at live symbols. "
        f"Stale entries: {stale_live}"
    )
    assert not stale_removed, (
        "REMOVED ledger entries must stay absent. "
        f"Reintroduced symbols: {stale_removed}"
    )


def test_final_1_0_compat_entries_have_specific_removal_conditions():
    banned_words = {"MIGRATE_NEXT", "TODO", "eventually", "later", "debt"}
    for entry in _LEDGER:
        upper_reason = entry.reason.upper()
        assert not any(word in upper_reason for word in banned_words), entry
        if entry.status == "KEEP_COMPAT_WITH_REMOVAL_CONDITION":
            assert "after" in entry.reason or "until" in entry.reason, entry
        if entry.status == "KEEP_PUBLIC_COMPAT":
            assert (
                "public" in entry.reason
                or "API" in entry.reason
                or "legacy" in entry.reason
            ), entry


def test_done_row_contracts_are_present_and_frozen():
    by_path: dict[str, list[str]] = {}
    for entry in _LEDGER:
        if entry.status == "DONE" and "." not in entry.symbol and entry.symbol[0].isupper():
            by_path.setdefault(entry.path, []).append(entry.symbol)

    for rel_path, symbols in by_path.items():
        tree = _tree(rel_path)
        classes = _classes(tree)
        frozen_flags = _dataclass_frozen_flags(tree)
        pydantic_forbid_classes = _pydantic_forbid_classes(tree)
        for symbol in symbols:
            assert symbol in classes
            assert frozen_flags.get(symbol) is True or symbol in pydantic_forbid_classes


def test_batch_094_public_dict_boundaries_are_classified():
    selected_paths = {
        "app/core/connections/db.py",
        "app/core/projects/db.py",
        "app/core/orchestrator/task_service.py",
        "app/core/results/db.py",
        "app/core/scheduler/db.py",
    }
    classified = {
        entry.symbol
        for entry in _LEDGER
        if entry.path in selected_paths
        and entry.status
        in {"KEEP_PUBLIC_COMPAT", "KEEP_COMPAT_WITH_REMOVAL_CONDITION", "BLOCKER"}
    }

    found = {
        symbol
        for rel_path in selected_paths
        for symbol in _public_dict_returns(rel_path)
    }

    assert found <= classified, (
        "Every remaining public dict-returning DB/core symbol in this Batch 094 "
        f"slice must be explicitly classified. Unclassified: {sorted(found - classified)}"
    )


def test_batch_094_done_functions_no_longer_return_public_dicts():
    for entry in _LEDGER:
        if entry.status != "DONE" or entry.symbol[0].isupper():
            continue
        returns = _function_returns(_tree(entry.path))
        annotation = returns.get(entry.symbol, "")
        assert "Dict" not in annotation and "dict" not in annotation


def test_project_core_domain_has_no_row_to_legacy_dict_crutch():
    """Project row dict conversion may not move into the core domain.

    Any remaining project dict compatibility belongs at API/public edges or in
    the explicitly blocked legacy config leaf while Batch 098 is incomplete.
    """
    project_core_paths = sorted((_APP_ROOT / "core" / "projects").glob("*.py"))
    found = {
        (_rel(path), symbol)
        for path in project_core_paths
        for symbol in _defined_symbols(_tree(_rel(path)))
        if symbol.endswith(".to_legacy_dict")
    }
    classified_blockers = {
        (entry.path, entry.symbol)
        for entry in _LEDGER
        if entry.status == "BLOCKER"
        and entry.path.startswith("app/core/projects/")
        and entry.symbol.endswith(".to_legacy_dict")
    }

    assert found == classified_blockers


def test_project_db_getattr_bridge_is_explicit_blocker_only():
    project_core_paths = sorted((_APP_ROOT / "core" / "projects").glob("*.py"))
    getattr_symbols = {
        (_rel(path), "__getattr__")
        for path in project_core_paths
        if "__getattr__" in _defined_symbols(_tree(_rel(path)))
    }

    assert not getattr_symbols
