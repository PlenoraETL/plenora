"""BE116 guard for broad dict annotations in Celery task boundaries."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable, NamedTuple

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

_TARGET_MODULES = (
    "backend/workers/tasks/carica_task.py",
    "backend/workers/tasks/enrichment_task.py",
    "backend/workers/tasks/imdf_publish_task.py",
    "backend/workers/tasks/pipeline_task.py",
)


class BroadDictAnnotation(NamedTuple):
    file: str
    qualname: str
    annotation: str
    kind: str


class TypingDictImport(NamedTuple):
    file: str
    name: str


def _parse(rel_path: str) -> ast.Module:
    return ast.parse((_REPO_ROOT / rel_path).read_text(encoding="utf-8-sig"))


def _annotation_contains_broad_dict(annotation: ast.AST | None) -> bool:
    if annotation is None:
        return False
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name) and node.id in {"Dict", "dict"}:
            return True
        if isinstance(node, ast.Attribute) and node.attr in {"Dict", "dict"}:
            return True
    return False


class _AnnotationVisitor(ast.NodeVisitor):
    def __init__(self, rel_path: str) -> None:
        self._rel_path = rel_path
        self._scope: list[str] = []
        self.hits: list[BroadDictAnnotation] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if _annotation_contains_broad_dict(node.annotation):
            self.hits.append(
                BroadDictAnnotation(
                    self._rel_path,
                    ".".join([*self._scope, ast.unparse(node.target)]),
                    ast.unparse(node.annotation),
                    "annassign",
                )
            )
        self.generic_visit(node)

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
                    BroadDictAnnotation(
                        self._rel_path,
                        ".".join([*self._scope, arg.arg]),
                        ast.unparse(arg.annotation),
                        "function_arg",
                    )
                )
        if _annotation_contains_broad_dict(node.returns):
            self.hits.append(
                BroadDictAnnotation(
                    self._rel_path,
                    ".".join(self._scope),
                    ast.unparse(node.returns),
                    "function_return",
                )
            )
        self.generic_visit(node)
        self._scope.pop()


class _TypingDictImportVisitor(ast.NodeVisitor):
    def __init__(self, rel_path: str) -> None:
        self._rel_path = rel_path
        self.hits: list[TypingDictImport] = []

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "typing":
            for alias in node.names:
                if alias.name == "Dict":
                    self.hits.append(TypingDictImport(self._rel_path, alias.name))
        self.generic_visit(node)


def _scan_annotations(paths: Iterable[str]) -> frozenset[BroadDictAnnotation]:
    hits: set[BroadDictAnnotation] = set()
    for rel_path in paths:
        visitor = _AnnotationVisitor(rel_path)
        visitor.visit(_parse(rel_path))
        hits.update(visitor.hits)
    return frozenset(hits)


def _scan_typing_dict_imports(paths: Iterable[str]) -> frozenset[TypingDictImport]:
    hits: set[TypingDictImport] = set()
    for rel_path in paths:
        visitor = _TypingDictImportVisitor(rel_path)
        visitor.visit(_parse(rel_path))
        hits.update(visitor.hits)
    return frozenset(hits)


def test_workers_tasks_have_no_broad_dict_annotations() -> None:
    assert _scan_annotations(_TARGET_MODULES) == frozenset()


def test_workers_tasks_have_no_typing_dict_imports() -> None:
    assert _scan_typing_dict_imports(_TARGET_MODULES) == frozenset()
