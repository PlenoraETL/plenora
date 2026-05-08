"""BE094 dict-boundary ratchet for selected core/db modules.

This is intentionally not a whole-repo dict ban. Routers and API payload
serialization remain out of scope; this guard tracks broad dict return
annotations only in the core/db boundary modules touched by BE Batch 094.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable, NamedTuple

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

_TARGET_BOUNDARY_MODULES_BE094: tuple[str, ...] = (
    "app/db/config/projects.py",
    "app/core/results/db.py",
    "app/db/config/schedules.py",
    "app/core/orchestrator/task_service.py",
    "app/core/connections/db.py",
    "app/core/connections/service.py",
    "app/core/connections/arcgis_connection_resolver.py",
)


class BroadDictReturn(NamedTuple):
    file: str
    qualname: str
    annotation: str


# Count report: 18 remaining broad dict-return residuals across the selected
# BE094 core/db boundary modules. Future batches should remove entries here
# as they replace compatibility dict wrappers with typed contracts.
EXPECTED_BROAD_DICT_RETURN_RESIDUALS_BE094_REMAINING_18: frozenset[
    BroadDictReturn
] = frozenset(
    {
        BroadDictReturn(
            "app/db/config/projects.py",
            "ProjectDetailRow.to_legacy_dict",
            "Dict[str, object]",
        ),
        BroadDictReturn(
            "app/db/config/projects.py",
            "ProjectDependencyRow.to_legacy_dict",
            "Dict[str, object]",
        ),
        BroadDictReturn(
            "app/db/config/projects.py",
            "ProjectCanvasCheckRow.to_legacy_dict",
            "Dict[str, object]",
        ),
        BroadDictReturn(
            "app/db/config/projects.py",
            "get_project",
            "Optional[Dict[str, object]]",
        ),
        BroadDictReturn("app/db/config/projects.py", "create_project", "Dict"),
        BroadDictReturn(
            "app/db/config/projects.py",
            "update_project",
            "Optional[Dict]",
        ),
        BroadDictReturn(
            "app/db/config/projects.py",
            "list_projects_using_connection",
            "List[Dict[str, object]]",
        ),
        BroadDictReturn(
            "app/db/config/projects.py",
            "list_projects_with_canvas_for_datasource_check",
            "List[Dict[str, object]]",
        ),
        BroadDictReturn(
            "app/core/results/db.py",
            "ResultMetadataRow.to_legacy_dict",
            "dict[str, object]",
        ),
        BroadDictReturn(
            "app/core/results/db.py",
            "save_result",
            "Optional[dict[str, object]]",
        ),
        BroadDictReturn(
            "app/core/results/db.py",
            "get_result",
            "Optional[dict[str, object]]",
        ),
        BroadDictReturn(
            "app/core/results/db.py",
            "get_latest_result",
            "Optional[dict[str, object]]",
        ),
        BroadDictReturn(
            "app/core/results/db.py",
            "list_results",
            "list[dict[str, object]]",
        ),
        BroadDictReturn(
            "app/db/config/schedules.py",
            "ScheduleRow.to_legacy_dict",
            "Dict[str, object]",
        ),
        BroadDictReturn(
            "app/db/config/schedules.py",
            "get_schedule",
            "Optional[Dict[str, object]]",
        ),
        BroadDictReturn(
            "app/db/config/schedules.py",
            "list_schedules",
            "List[Dict[str, object]]",
        ),
        BroadDictReturn(
            "app/core/orchestrator/task_service.py",
            "ActiveTaskInfo.to_dict",
            "Dict[str, str]",
        ),
        BroadDictReturn(
            "app/core/connections/db.py",
            "ConnectionConfigRow.to_legacy_dict",
            "dict[str, object]",
        ),
    }
)


BROAD_DICT_RETURN_CLASSIFICATIONS_BE094: dict[BroadDictReturn, str] = {
    residual: (
        "KEEP_PUBLIC_COMPAT"
        if residual.qualname.endswith(("to_legacy_dict", "to_dict"))
        else "KEEP_COMPAT_WITH_REMOVAL_CONDITION"
    )
    for residual in EXPECTED_BROAD_DICT_RETURN_RESIDUALS_BE094_REMAINING_18
}

TYPED_ZERO_COUNT_MODULES_BE094_DONE: frozenset[str] = frozenset(
    {
        "app/core/connections/service.py",
        "app/core/connections/arcgis_connection_resolver.py",
    }
)

_ALLOWED_CLASSIFICATIONS = frozenset(
    {
        "DONE",
        "REMOVED",
        "KEEP_PUBLIC_COMPAT",
        "KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        "BLOCKER",
    }
)


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def _annotation_contains_broad_dict(annotation: ast.AST | None) -> bool:
    if annotation is None:
        return False
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name) and node.id in {"Dict", "dict"}:
            return True
        if isinstance(node, ast.Attribute) and node.attr in {"Dict", "dict"}:
            return True
    return False


class _BroadDictReturnVisitor(ast.NodeVisitor):
    def __init__(self, rel_path: str) -> None:
        self._rel_path = rel_path
        self._scope: list[str] = []
        self.hits: list[BroadDictReturn] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if _annotation_contains_broad_dict(node.returns):
            qualname = ".".join([*self._scope, node.name])
            self.hits.append(
                BroadDictReturn(
                    self._rel_path,
                    qualname,
                    ast.unparse(node.returns),
                )
            )
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()


def _scan_broad_dict_returns(paths: Iterable[str]) -> frozenset[BroadDictReturn]:
    hits: set[BroadDictReturn] = set()
    for rel_path in paths:
        visitor = _BroadDictReturnVisitor(rel_path)
        visitor.visit(_parse(_BACKEND_ROOT / rel_path))
        hits.update(visitor.hits)
    return frozenset(hits)


def test_be094_selected_boundary_broad_dict_returns_match_remaining_18() -> None:
    """Ratchet selected core/db boundary broad-dict returns.

    Extra entries mean new broad dict returns crossed one of the selected
    service/repository boundaries. Missing entries mean a future batch removed
    residuals and should lower the ``REMAINING_18`` baseline.
    """
    found = _scan_broad_dict_returns(_TARGET_BOUNDARY_MODULES_BE094)
    expected = EXPECTED_BROAD_DICT_RETURN_RESIDUALS_BE094_REMAINING_18

    extra = sorted(found - expected)
    missing = sorted(expected - found)

    assert not extra, (
        "New broad dict return annotations detected in selected BE094 "
        f"core/db boundary modules: {extra}. Use typed row/contract objects "
        "or explicitly classify a compatibility wrapper."
    )
    assert not missing, (
        "Broad dict return residuals were removed from selected BE094 "
        f"boundary modules: {missing}. Ratchet the count below 18 by "
        "updating EXPECTED_BROAD_DICT_RETURN_RESIDUALS_BE094_REMAINING_18."
    )


def test_be094_dict_boundary_residual_classifications_are_limited() -> None:
    """Residual ledger uses only the BE094-approved classification vocabulary."""
    found = _scan_broad_dict_returns(_TARGET_BOUNDARY_MODULES_BE094)
    assert set(BROAD_DICT_RETURN_CLASSIFICATIONS_BE094) == set(found)
    assert set(BROAD_DICT_RETURN_CLASSIFICATIONS_BE094.values()) <= _ALLOWED_CLASSIFICATIONS

    typed_zero_modules = {
        rel_path
        for rel_path in _TARGET_BOUNDARY_MODULES_BE094
        if not any(residual.file == rel_path for residual in found)
    }
    assert typed_zero_modules == set(TYPED_ZERO_COUNT_MODULES_BE094_DONE)
