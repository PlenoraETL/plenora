"""BE109 Wave P guard for selected boundary dict parameters and fields."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable, NamedTuple

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

_TARGET_BOUNDARY_MODULES = (
    "app/core/imdf/validator.py",
    "app/core/imdf/storage.py",
    "app/core/imdf/router.py",
    "app/core/imdf/publish_dispatcher.py",
    "app/core/imdf/arcgis_uploader.py",
    "app/core/imdf/arcgis/publish_ops.py",
    "app/core/carica/router.py",
    "app/core/carica/models.py",
    "app/core/carica/executor.py",
    "app/core/carica/conflict_request_loader.py",
    "app/core/carica/column_mapper.py",
    "app/core/carica/ddl_generator.py",
    "app/core/carica/writer_preflight_lifecycle.py",
    "app/core/carica/writer_replace_strategies.py",
    "app/core/orchestrator/router.py",
    "app/core/orchestrator/service.py",
    "app/core/orchestrator/task_service.py",
    "app/core/eseguibili/models.py",
    "app/core/eseguibili/scanner.py",
    "app/core/eseguibili/db.py",
    "app/core/tickets/attachment_boundary.py",
)


class BroadDictBoundaryAnnotation(NamedTuple):
    file: str
    qualname: str
    annotation: str
    kind: str


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


class _BoundaryAnnotationVisitor(ast.NodeVisitor):
    def __init__(self, rel_path: str) -> None:
        self._rel_path = rel_path
        self._scope: list[str] = []
        self.hits: list[BroadDictBoundaryAnnotation] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and _annotation_contains_broad_dict(stmt.annotation):
                target = ast.unparse(stmt.target)
                self.hits.append(
                    BroadDictBoundaryAnnotation(
                        self._rel_path,
                        ".".join([*self._scope, target]),
                        ast.unparse(stmt.annotation),
                        "class_field",
                    )
                )
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._scope.append(node.name)
        args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if node.args.vararg is not None:
            args.append(node.args.vararg)
        if node.args.kwarg is not None:
            args.append(node.args.kwarg)
        for arg in args:
            if _annotation_contains_broad_dict(arg.annotation):
                self.hits.append(
                    BroadDictBoundaryAnnotation(
                        self._rel_path,
                        ".".join([*self._scope, arg.arg]),
                        ast.unparse(arg.annotation),
                        "function_arg",
                    )
                )
        self.generic_visit(node)
        self._scope.pop()


def _scan_boundary_annotations(paths: Iterable[str]) -> frozenset[BroadDictBoundaryAnnotation]:
    hits: set[BroadDictBoundaryAnnotation] = set()
    for rel_path in paths:
        visitor = _BoundaryAnnotationVisitor(rel_path)
        visitor.visit(_parse(rel_path))
        hits.update(visitor.hits)
    return frozenset(hits)


def test_selected_boundary_modules_have_no_broad_dict_parameters_or_fields() -> None:
    assert _scan_boundary_annotations(_TARGET_BOUNDARY_MODULES) == frozenset()
