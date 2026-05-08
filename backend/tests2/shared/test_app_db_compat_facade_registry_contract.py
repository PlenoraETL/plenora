"""Tombstone guard for removed `app.db` DB compatibility facades."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.shared]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_SCAN_ROOTS = (
    _BACKEND_ROOT / "app",
    _BACKEND_ROOT / "workers",
    _BACKEND_ROOT / "tests2",
)
_TOMBSTONED_FACADES = frozenset(
    {
        "app.db.config_db_service",
        "app.db.results_db_service",
    }
)


def _parse(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8-sig"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def _rel(path: Path) -> str:
    return path.relative_to(_BACKEND_ROOT).as_posix()


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


@pytest.mark.parametrize("facade_module", sorted(_TOMBSTONED_FACADES))
def test_removed_db_facade_modules_stay_absent(facade_module: str):
    assert importlib.util.find_spec(facade_module) is None


@pytest.mark.parametrize("facade_module", sorted(_TOMBSTONED_FACADES))
def test_removed_db_facades_have_no_importers(facade_module: str):
    offenders: list[str] = []
    for root in _SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if path == Path(__file__).resolve():
                continue
            tree = _parse(path)
            if tree is not None and _imports_facade(tree, facade_module):
                offenders.append(_rel(path))

    assert not offenders, (
        f"`{facade_module}` is deleted. Import the canonical app.core "
        "domain boundary instead.\nViolations:\n  - "
        + "\n  - ".join(sorted(offenders))
    )
