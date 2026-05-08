"""Tombstone contract for the removed `app.db.config_db_service` facade."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_SCAN_ROOTS = (
    _BACKEND_ROOT / "app",
    _BACKEND_ROOT / "workers",
    _BACKEND_ROOT / "tests2",
)
_FACADE_DOTTED = "app.db.config_db_service"
_FACADE_PARENT = "app.db"
_FACADE_LEAF = "config_db_service"
_FACADE_FILE = "app/db/config_db_service.py"


def _rel(path: Path) -> str:
    return path.relative_to(_BACKEND_ROOT).as_posix()


def _parse(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8-sig"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def _imports_facade(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == _FACADE_DOTTED for alias in node.names):
                return True
            continue
        if isinstance(node, ast.ImportFrom):
            if node.module == _FACADE_DOTTED:
                return True
            if node.module == _FACADE_PARENT:
                return any(alias.name == _FACADE_LEAF for alias in node.names)
    return False


def _facade_importers() -> list[str]:
    current_file = Path(__file__).resolve()
    importers: list[str] = []
    for root in _SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if path.resolve() == current_file:
                continue
            tree = _parse(path)
            if tree is not None and _imports_facade(tree):
                importers.append(_rel(path))
    return sorted(importers)


def test_config_db_service_module_is_tombstoned() -> None:
    assert importlib.util.find_spec(_FACADE_DOTTED) is None
    assert not (_BACKEND_ROOT / _FACADE_FILE).exists()


def test_no_config_db_service_imports_anywhere() -> None:
    importers = _facade_importers()

    assert not importers, (
        "Deleted facade `app.db.config_db_service` is imported again. "
        "Use the domain-owned app.core.<domain>.db boundary instead.\n"
        "Violations:\n  - " + "\n  - ".join(importers)
    )
