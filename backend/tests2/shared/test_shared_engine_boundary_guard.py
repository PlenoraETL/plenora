"""
Architecture guard - app.shared engine subpackages must stay clean.

BE-GO-READY-ARCH-057-W1-SHARED-FOLDER-SHAPE-AND-GUARD
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.shared]

_SHARED_ROOT = Path(__file__).parents[2] / "app" / "shared"

_ENGINE_SUBPACKAGES = ("execution", "resources", "connections")

_FORBIDDEN_PREFIXES = (
    "app.core",
    "app.db",
    "fastapi",
    "starlette",
    "pandas",
    "geopandas",
    "fiona",
    "shapely",
    "polars",
)


def _engine_python_files() -> list[Path]:
    files: list[Path] = []
    for pkg in _ENGINE_SUBPACKAGES:
        subdir = _SHARED_ROOT / pkg
        if subdir.is_dir():
            files.extend(sorted(subdir.rglob("*.py")))
    return files


def _imports_in_file(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
    return modules


@pytest.mark.parametrize(
    "src_file",
    _engine_python_files(),
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_shared_engine_module_has_no_heavy_imports(src_file: Path) -> None:
    """Engine subpackage modules must not import vertical or heavy runtime code."""
    for module in _imports_in_file(src_file):
        for prefix in _FORBIDDEN_PREFIXES:
            assert not module.startswith(prefix), (
                f"app/shared/{src_file.parent.name}/{src_file.name}: "
                f"forbidden import {module!r} (starts with {prefix!r}). "
                "Engine subpackage contracts must be leaf modules. "
                "See BE-GO-READY-ARCH-057-W1 and BE_RULES.md."
            )
