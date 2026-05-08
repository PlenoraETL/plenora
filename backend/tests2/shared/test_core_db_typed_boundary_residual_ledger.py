"""1.0 residual ledger for typed core/DB boundary seams.

This is intentionally narrow: it locks the Batch 094 DB/core rows that were
typed, plus the compatibility wrappers still returning public dict shapes.
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
        "app/db/config/projects.py",
        "ProjectRow",
        "DONE",
        "project list DB rows are immutable dataclasses",
    ),
    BoundaryEntry(
        "app/db/config/projects.py",
        "ProjectDetailRow",
        "DONE",
        "project detail DB rows are immutable dataclasses",
    ),
    BoundaryEntry(
        "app/db/config/projects.py",
        "ProjectDependencyRow",
        "DONE",
        "connection-delete dependency checks have typed rows",
    ),
    BoundaryEntry(
        "app/db/config/projects.py",
        "ProjectCanvasCheckRow",
        "DONE",
        "datasource cascade checks have typed project rows",
    ),
    BoundaryEntry(
        "app/db/config/projects.py",
        "get_project",
        "KEEP_PUBLIC_COMPAT",
        "legacy API/test wrapper returns dict until callers migrate to get_project_row",
    ),
    BoundaryEntry(
        "app/db/config/projects.py",
        "create_project",
        "KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        "write path still returns legacy dict; remove after ProjectService CRUD consumes typed post-write rows",
    ),
    BoundaryEntry(
        "app/db/config/projects.py",
        "update_project",
        "KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        "write path still returns legacy dict; remove after ProjectService CRUD consumes typed post-write rows",
    ),
    BoundaryEntry(
        "app/db/config/projects.py",
        "list_projects_using_connection",
        "KEEP_PUBLIC_COMPAT",
        "legacy dependency-check wrapper delegates to ProjectDependencyRow",
    ),
    BoundaryEntry(
        "app/db/config/projects.py",
        "list_projects_with_canvas_for_datasource_check",
        "KEEP_PUBLIC_COMPAT",
        "legacy datasource-check wrapper delegates to ProjectCanvasCheckRow",
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
        "app/db/config/schedules.py",
        "ScheduleRow",
        "DONE",
        "schedule DB reads and post-write reads use immutable row contracts",
    ),
    BoundaryEntry(
        "app/db/config/schedules.py",
        "get_schedule",
        "KEEP_PUBLIC_COMPAT",
        "legacy wrapper delegates to get_schedule_row",
    ),
    BoundaryEntry(
        "app/db/config/schedules.py",
        "list_schedules",
        "KEEP_PUBLIC_COMPAT",
        "legacy wrapper delegates to list_schedule_rows",
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
        "dict conversion stays explicit for external callers that still need it",
    ),
    BoundaryEntry(
        "app/db/config/connections.py",
        "get_connection_config",
        "KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        "legacy config leaf returns dict until all consumers use app.core.connections.db",
    ),
    BoundaryEntry(
        "app/db/config/connections.py",
        "list_connections",
        "KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        "legacy config leaf returns dict until list callers use typed core bridge",
    ),
    BoundaryEntry(
        "app/db/config/connections.py",
        "create_connection",
        "KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        "legacy config leaf returns dict until writes hydrate ConnectionConfigRow directly",
    ),
    BoundaryEntry(
        "app/db/config/connections.py",
        "update_connection",
        "KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        "legacy config leaf returns dict until writes hydrate ConnectionConfigRow directly",
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
        "get_task_result",
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
        "workers/tasks/carica_task.py",
        "_execute_arcgis_write",
        "KEEP_PUBLIC_COMPAT",
        "Carica worker delegates ArcGIS writes to the domain boundary but still returns Celery result dict shape",
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
)


def _tree(rel_path: str) -> ast.Module:
    return ast.parse((_BACKEND_ROOT / rel_path).read_text(encoding="utf-8-sig"))


def _classes(tree: ast.AST) -> set[str]:
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}


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


def test_done_row_contracts_are_present_and_frozen():
    by_path: dict[str, list[str]] = {}
    for entry in _LEDGER:
        if entry.status == "DONE" and entry.symbol[0].isupper():
            by_path.setdefault(entry.path, []).append(entry.symbol)

    for rel_path, symbols in by_path.items():
        tree = _tree(rel_path)
        classes = _classes(tree)
        frozen_flags = _dataclass_frozen_flags(tree)
        for symbol in symbols:
            assert symbol in classes
            assert frozen_flags.get(symbol) is True


def test_batch_094_public_dict_boundaries_are_classified():
    selected_paths = {
        "app/core/connections/db.py",
        "app/core/orchestrator/task_service.py",
        "app/core/results/db.py",
        "app/db/config/connections.py",
        "app/db/config/projects.py",
        "app/db/config/schedules.py",
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
