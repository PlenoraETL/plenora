"""Tombstone guards for legacy model shims."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.shared]


_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_PROD_ROOTS = (_BACKEND_ROOT / "app", _BACKEND_ROOT / "workers")
_TEST_ROOT = _BACKEND_ROOT / "tests2"

def _rel(path: Path) -> str:
    return path.relative_to(_BACKEND_ROOT).as_posix()


def _iter_python(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def _parse(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8-sig"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def _imports_module(path: Path, module_name: str) -> bool:
    tree = _parse(path)
    if tree is None:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name == module_name or alias.name.startswith(f"{module_name}.")
                for alias in node.names
            ):
                return True
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == module_name or node.module.startswith(f"{module_name}."):
                return True
    return False


def _module_importers(roots: tuple[Path, ...], module_name: str) -> set[str]:
    hits: set[str] = set()
    for root in roots:
        for path in _iter_python(root):
            if path == _BACKEND_ROOT / "app" / "models.py":
                continue
            if path == _BACKEND_ROOT / "app" / "shared" / "models.py":
                continue
            if _imports_module(path, module_name):
                hits.add(_rel(path))
    return hits


def test_no_tests_import_legacy_app_models_shim() -> None:
    hits = sorted(_module_importers((_TEST_ROOT,), "app.models"))

    assert not hits, (
        "Tests must import models from app.shared.models or a domain-owned "
        "models module, not the legacy app.models shim.\nViolations:\n  - "
        + "\n  - ".join(hits)
    )


def test_no_production_imports_of_legacy_app_models_shim() -> None:
    hits = sorted(_module_importers(_PROD_ROOTS, "app.models"))

    assert not hits, (
        "Production imports from app.models are forbidden. Use "
        "app.shared.models or a domain-owned models module instead.\n"
        "Violations:\n  - "
        + "\n  - ".join(hits)
    )


def test_app_models_shim_is_removed() -> None:
    shim = _BACKEND_ROOT / "app" / "models.py"
    assert not shim.exists()


def test_shared_models_shim_reexports_canvas_models() -> None:
    from app.core.projects import canvas_models
    from app.shared import models as shared_models

    assert shared_models.CanvasState is canvas_models.CanvasState
    assert shared_models.Node is canvas_models.Node
    assert shared_models.Edge is canvas_models.Edge
    assert shared_models.ColumnInfo is canvas_models.ColumnInfo


def test_no_production_imports_of_shared_models_compat_shim() -> None:
    hits = sorted(_module_importers(_PROD_ROOTS, "app.shared.models"))

    assert not hits, (
        "Production imports from app.shared.models are forbidden. Use "
        "app.core.projects.canvas_models instead.\nViolations:\n  - "
        + "\n  - ".join(hits)
    )
