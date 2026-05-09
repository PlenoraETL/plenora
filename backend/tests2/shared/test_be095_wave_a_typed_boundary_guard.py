"""BE095 Wave A typed-boundary guard and residual ratchet.

The guard is intentionally narrow: it scans only the Wave A files whose
boundaries were changed or protected in this wave. It is not a whole-repo dict
ban.
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


_WAVE_A_TARGETS: tuple[str, ...] = (
    "workers/tasks/carica_task.py",
    "app/core/features/arcgis_feature_operations.py",
    "app/core/features/arcgis_writer_boundary.py",
    "app/core/features/features_service.py",
    "app/core/orchestrator/task_service.py",
    "app/core/orchestrator/router_mappers.py",
    "app/core/orchestrator/router.py",
)


_EXPECTED_WAVE_A_BROAD_DICT_RETURNS: frozenset[BroadDictReturn] = frozenset(
    {
        BroadDictReturn("workers/tasks/carica_task.py", "execute_carica_task", "Dict[str, Any]"),
        BroadDictReturn("workers/tasks/carica_task.py", "_execute_rest_write", "Dict[str, Any]"),
        BroadDictReturn("app/core/features/features_service.py", "FeaturesService.browse_services", "List[dict]"),
        BroadDictReturn("app/core/features/features_service.py", "FeaturesService.list_folders", "List[dict]"),
        BroadDictReturn("app/core/features/features_service.py", "FeaturesService.search_services", "List[dict]"),
        BroadDictReturn("app/core/features/features_service.py", "FeaturesService.browse_layers", "List[dict]"),
        BroadDictReturn("app/core/features/features_service.py", "FeaturesService.get_layer_schema", "dict"),
        BroadDictReturn("app/core/features/features_service.py", "FeaturesService.execute_query", "Dict[str, Any]"),
        BroadDictReturn("app/core/features/features_service.py", "FeaturesService.write_features", "dict"),
        BroadDictReturn("app/core/features/features_service.py", "FeaturesService.create_service", "dict"),
        BroadDictReturn("app/core/features/features_service.py", "FeaturesService._write_features_df_core", "dict"),
        BroadDictReturn("app/core/features/features_service.py", "FeaturesService._resolve_objectids_for_update", "List[dict]"),
        BroadDictReturn("app/core/orchestrator/task_service.py", "ActiveTaskInfo.to_dict", "Dict[str, str]"),
        BroadDictReturn("app/core/orchestrator/router_mappers.py", "task_result_to_dict", "Dict[str, Any]"),
        BroadDictReturn("app/core/orchestrator/router.py", "_rest_carica_parser_input", "Dict[str, Any]"),
        BroadDictReturn("app/core/orchestrator/router.py", "execute_project_pipeline", "Dict[str, Any]"),
        BroadDictReturn("app/core/orchestrator/router.py", "preview_project", "Dict[str, Any]"),
        BroadDictReturn("app/core/orchestrator/router.py", "cancel_task", "Dict[str, str]"),
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


WAVE_A_BROAD_DICT_CLASSIFICATIONS: dict[BroadDictReturn, str] = {
    residual: (
        "KEEP_PUBLIC_COMPAT"
        if residual.file
        in {
            "app/core/orchestrator/router.py",
            "app/core/orchestrator/router_mappers.py",
        }
        or residual.qualname.endswith(("to_dict", "write_features_from_df", "create_service_from_df"))
        else "KEEP_COMPAT_WITH_REMOVAL_CONDITION"
    )
    for residual in _EXPECTED_WAVE_A_BROAD_DICT_RETURNS
}


@dataclass(frozen=True)
class _FunctionScope:
    name: str
    node: ast.FunctionDef | ast.AsyncFunctionDef


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


def _functions_by_name(tree: ast.Module) -> dict[str, _FunctionScope]:
    found: dict[str, _FunctionScope] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = _FunctionScope(node.name, node)
    return found


def test_wave_a_broad_dict_returns_match_selected_residuals() -> None:
    found = _scan_broad_dict_returns(_WAVE_A_TARGETS)
    extra = sorted(found - _EXPECTED_WAVE_A_BROAD_DICT_RETURNS)
    missing = sorted(_EXPECTED_WAVE_A_BROAD_DICT_RETURNS - found)

    assert not extra, (
        "New broad dict return annotations detected in BE095 Wave A targets: "
        f"{extra}. Use typed contracts or classify a compatibility wrapper."
    )
    assert not missing, (
        "BE095 Wave A broad dict residuals were removed: "
        f"{missing}. Ratchet _EXPECTED_WAVE_A_BROAD_DICT_RETURNS down."
    )


def test_wave_a_broad_dict_classifications_use_1_0_vocabulary() -> None:
    found = _scan_broad_dict_returns(_WAVE_A_TARGETS)
    assert set(WAVE_A_BROAD_DICT_CLASSIFICATIONS) == set(found)
    assert set(WAVE_A_BROAD_DICT_CLASSIFICATIONS.values()) <= _ALLOWED_CLASSIFICATIONS


def test_task_result_payload_no_longer_has_broad_payload_field() -> None:
    tree = _parse("app/core/orchestrator/task_service.py")
    classes = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    payload_class = classes["TaskResultPayload"]

    field_annotations = {
        node.target.id: ast.unparse(node.annotation)
        for node in payload_class.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }

    assert "payload" not in field_annotations
    assert all("Mapping[str, object]" not in annotation for annotation in field_annotations.values())
    assert all("Dict[str, object]" not in annotation for annotation in field_annotations.values())
    assert "public_fields" in field_annotations


def test_carica_worker_arcgis_branch_stays_on_writer_boundary() -> None:
    tree = _parse("workers/tasks/carica_task.py")
    functions = _functions_by_name(tree)
    arcgis_write = functions["_execute_arcgis_write"].node

    forbidden_calls = {
        "ArcGISClient",
        "create_client",
        "create_arcgis_provider",
        "dataframe_to_esri_features",
        "add_features",
        "update_features",
        "delete_features",
        "create_service",
        "query_oid_by_key",
    }
    required_domain_calls = {
        "CaricaArcGISWriteRequest",
        "create_default_arcgis_feature_writer",
        "execute_carica_arcgis_write",
        "to_task_result",
    }

    violations: list[tuple[int, str]] = []
    domain_calls: set[str] = set()
    for node in ast.walk(arcgis_write):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        call_name = ""
        if isinstance(func, ast.Name):
            call_name = func.id
        elif isinstance(func, ast.Attribute):
            call_name = func.attr
        if call_name in required_domain_calls:
            domain_calls.add(call_name)
        if call_name in forbidden_calls:
            violations.append((node.lineno, call_name))

    assert domain_calls == required_domain_calls
    assert not violations, (
        "Carica worker ArcGIS branch must delegate provider-specific writes to "
        f"app.core.carica.arcgis_write_boundary. Forbidden calls: {violations}"
    )
