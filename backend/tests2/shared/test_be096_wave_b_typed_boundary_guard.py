"""BE096 Wave B typed-boundary guard and residual ratchet.

This guard is intentionally narrow: it scans only Wave B target modules for
eseguibili, admin status, result metadata writes, project writes, and
connection writes. It is not a whole-repo dict ban.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, NamedTuple

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


class BroadDictReturn(NamedTuple):
    file: str
    qualname: str
    annotation: str


_WAVE_B_TARGETS: tuple[str, ...] = (
    "app/core/eseguibili/db.py",
    "app/core/eseguibili/service.py",
    "app/core/eseguibili/router.py",
    "app/core/admin_system_status/service.py",
    "app/core/admin_system_status/router.py",
    "app/core/results/db.py",
    "app/core/projects/service.py",
    "app/core/projects/db.py",
    "app/core/connections/db.py",
    "app/core/connections/service.py",
)


_EXPECTED_WAVE_B_BROAD_DICT_RETURNS: frozenset[BroadDictReturn] = frozenset(
    {
        BroadDictReturn("app/core/eseguibili/service.py", "analyze_file_upload", "Dict[str, Any]"),
        BroadDictReturn("app/core/eseguibili/service.py", "execute", "Dict[str, Any]"),
        BroadDictReturn("app/core/eseguibili/db.py", "EseguibileRow.to_legacy_dict", "Dict[str, Any]"),
        BroadDictReturn("app/core/eseguibili/db.py", "EseguibileSummaryRow.to_legacy_dict", "Dict[str, Any]"),
        BroadDictReturn("app/core/eseguibili/db.py", "EseguibileSourceRefRow.to_legacy_dict", "Dict[str, str]"),
        BroadDictReturn("app/core/eseguibili/router.py", "_summary_response", "dict"),
        BroadDictReturn("app/core/eseguibili/router.py", "_details_response", "dict"),
        BroadDictReturn("app/core/eseguibili/router.py", "_load_upload_meta", "Optional[dict]"),
        BroadDictReturn("app/core/eseguibili/router.py", "_load_task_owner", "Optional[dict]"),
        BroadDictReturn("app/core/eseguibili/router.py", "_to_details", "dict"),
        BroadDictReturn("app/core/admin_system_status/router.py", "get_system_status", "dict[str, object]"),
        BroadDictReturn("app/core/results/db.py", "ResultMetadataRow.to_legacy_dict", "dict[str, object]"),
        BroadDictReturn("app/core/results/db.py", "save_result", "Optional[dict[str, object]]"),
        BroadDictReturn("app/core/results/db.py", "get_result", "Optional[dict[str, object]]"),
        BroadDictReturn("app/core/results/db.py", "get_latest_result", "Optional[dict[str, object]]"),
        BroadDictReturn("app/core/results/db.py", "list_results", "list[dict[str, object]]"),
        BroadDictReturn("app/core/projects/service.py", "ProjectService.get_project", "Optional[dict]"),
        BroadDictReturn("app/core/projects/service.py", "ProjectService._find_dependent_projects", "List[dict]"),
        BroadDictReturn("app/core/projects/service.py", "ProjectService.get_latest_result", "Optional[dict]"),
        BroadDictReturn("app/core/projects/service.py", "ProjectService.list_results", "List[dict]"),
        BroadDictReturn("app/core/projects/db.py", "_detail_compat_payload", "dict[str, object]"),
        BroadDictReturn("app/core/projects/db.py", "_dependency_compat_payload", "dict[str, object]"),
        BroadDictReturn("app/core/projects/db.py", "_canvas_check_compat_payload", "dict[str, object]"),
        BroadDictReturn("app/core/projects/db.py", "get_project", "dict[str, object] | None"),
        BroadDictReturn("app/core/projects/db.py", "create_project", "dict[str, object]"),
        BroadDictReturn("app/core/projects/db.py", "update_project", "dict[str, object] | None"),
        BroadDictReturn("app/core/projects/db.py", "list_projects_using_connection", "list[dict[str, object]]"),
        BroadDictReturn("app/core/projects/db.py", "list_projects_with_canvas_for_datasource_check", "list[dict[str, object]]"),
        BroadDictReturn("app/core/connections/db.py", "ConnectionConfigRow.to_legacy_dict", "dict[str, object]"),
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


@dataclass(frozen=True)
class ResidualClassification:
    status: str
    removal_condition: str


WAVE_B_BROAD_DICT_CLASSIFICATIONS: dict[BroadDictReturn, ResidualClassification] = {
    residual: ResidualClassification(
        status="KEEP_PUBLIC_COMPAT",
        removal_condition="public API serializer or legacy compatibility helper owns this dict shape",
    )
    for residual in _EXPECTED_WAVE_B_BROAD_DICT_RETURNS
    if residual.file.endswith("router.py")
    or residual.qualname.endswith(("to_legacy_dict", "get_project", "get_latest_result", "list_results"))
}

WAVE_B_BROAD_DICT_CLASSIFICATIONS.update(
    {
        residual: ResidualClassification(
            status="KEEP_COMPAT_WITH_REMOVAL_CONDITION",
            removal_condition=(
                "remove after this Wave B boundary returns frozen row or payload "
                "models and router/API code owns dict serialization"
            ),
        )
        for residual in _EXPECTED_WAVE_B_BROAD_DICT_RETURNS
        if residual not in WAVE_B_BROAD_DICT_CLASSIFICATIONS
    }
)

_DONE_ZERO_BROAD_DICT_MODULES: frozenset[str] = frozenset(
    {
        "app/core/admin_system_status/service.py",
        "app/core/connections/service.py",
    }
)

_BANNED_RESIDUAL_WORDS = frozenset({"MIGRATE_NEXT", "TODO", "eventually", "later"})


def _parse(rel_path: str) -> ast.Module:
    return ast.parse((_BACKEND_ROOT / rel_path).read_text(encoding="utf-8-sig"))


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
            self.hits.append(
                BroadDictReturn(
                    self._rel_path,
                    ".".join([*self._scope, node.name]),
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
        visitor.visit(_parse(rel_path))
        hits.update(visitor.hits)
    return frozenset(hits)


def _imported_modules(rel_path: str) -> set[str]:
    tree = _parse(rel_path)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_wave_b_broad_dict_returns_match_selected_residuals() -> None:
    found = _scan_broad_dict_returns(_WAVE_B_TARGETS)
    extra = sorted(found - _EXPECTED_WAVE_B_BROAD_DICT_RETURNS)
    missing = sorted(_EXPECTED_WAVE_B_BROAD_DICT_RETURNS - found)

    assert not extra, (
        "New broad dict return annotations detected in BE096 Wave B targets: "
        f"{extra}. Use typed contracts or classify a compatibility wrapper."
    )
    assert not missing, (
        "BE096 Wave B broad dict residuals were removed: "
        f"{missing}. Ratchet _EXPECTED_WAVE_B_BROAD_DICT_RETURNS down."
    )


def test_wave_b_residual_classifications_use_1_0_vocabulary() -> None:
    found = _scan_broad_dict_returns(_WAVE_B_TARGETS)
    assert set(WAVE_B_BROAD_DICT_CLASSIFICATIONS) == set(found)
    assert {
        classification.status
        for classification in WAVE_B_BROAD_DICT_CLASSIFICATIONS.values()
    } <= _ALLOWED_CLASSIFICATIONS


def test_wave_b_residual_removal_conditions_are_specific() -> None:
    for residual, classification in WAVE_B_BROAD_DICT_CLASSIFICATIONS.items():
        assert classification.removal_condition
        upper_condition = classification.removal_condition.upper()
        assert not any(word in upper_condition for word in _BANNED_RESIDUAL_WORDS), (
            f"{residual} has vague residual wording: "
            f"{classification.removal_condition!r}"
        )
        if classification.status == "KEEP_COMPAT_WITH_REMOVAL_CONDITION":
            assert "after" in classification.removal_condition


def test_wave_b_zero_broad_dict_modules_stay_zero() -> None:
    found = _scan_broad_dict_returns(_WAVE_B_TARGETS)
    zero_modules = {
        rel_path
        for rel_path in _WAVE_B_TARGETS
        if not any(residual.file == rel_path for residual in found)
    }
    assert zero_modules == set(_DONE_ZERO_BROAD_DICT_MODULES)


def test_connection_and_project_services_use_domain_db_bridges() -> None:
    connection_imports = _imported_modules("app/core/connections/service.py")
    project_imports = _imported_modules("app/core/projects/service.py")

    assert "app.db.config.connections" not in connection_imports
    assert "app.db.pg_connection" not in connection_imports
    assert "app.core.connections" in connection_imports

    assert "app.core.projects.db" not in project_imports
    assert "app.core.projects" in project_imports
