"""BE103 Wave H+I typed-boundary guard for logs/results/intermediate/artifacts."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable, NamedTuple

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_TARGET_ROOTS = (
    _BACKEND_ROOT / "app/core/logs",
    _BACKEND_ROOT / "app/core/results",
    _BACKEND_ROOT / "app/core/intermediate",
    _BACKEND_ROOT / "app/core/artifacts",
)


class BroadDictReturn(NamedTuple):
    file: str
    qualname: str
    annotation: str


def _target_modules() -> tuple[str, ...]:
    paths: list[str] = []
    for root in _TARGET_ROOTS:
        paths.extend(
            str(path.relative_to(_BACKEND_ROOT)).replace("\\", "/")
            for path in root.rglob("*.py")
        )
    return tuple(sorted(paths))


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


def test_wave_h_i_modules_have_zero_broad_dict_returns() -> None:
    assert _scan_broad_dict_returns(_target_modules()) == frozenset()
