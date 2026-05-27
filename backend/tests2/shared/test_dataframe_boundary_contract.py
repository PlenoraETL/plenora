"""Architecture contract for the dataframe engine boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _BACKEND_ROOT / "app"
_DATAFRAME_BOUNDARY = _APP_ROOT / "core" / "dataframes"
_FORBIDDEN_ENGINE_ROOTS = {"pandas", "geopandas"}
_ALLOWED_VERTICAL_IMPORTS: set[str] = set()


def _python_files_under_app() -> list[Path]:
    return [
        path
        for path in sorted(_APP_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def _import_roots(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.extend(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.append(node.module.split(".", 1)[0])
    return roots


def _dynamic_import_roots(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        is_dynamic_import = (
            isinstance(func, ast.Name)
            and func.id == "__import__"
        ) or (
            isinstance(func, ast.Attribute)
            and func.attr == "import_module"
        )
        if not is_dynamic_import:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            roots.append((node.lineno, arg.value.split(".", 1)[0]))
    return roots


def _is_inside_dataframe_boundary(path: Path) -> bool:
    try:
        path.relative_to(_DATAFRAME_BOUNDARY)
        return True
    except ValueError:
        return False


def test_pandas_and_geopandas_imports_stay_inside_dataframe_boundary() -> None:
    """Only app.core.dataframes may import the current dataframe engine.

    The rest of the backend must depend on helpers/ports from
    ``app.core.dataframes`` instead of importing pandas/geopandas directly.
    This keeps the current pandas-backed implementation replaceable.
    """
    offenders: list[str] = []
    for path in _python_files_under_app():
        if _is_inside_dataframe_boundary(path):
            continue
        forbidden = sorted(_FORBIDDEN_ENGINE_ROOTS & set(_import_roots(path)))
        if forbidden:
            rel = path.relative_to(_BACKEND_ROOT).as_posix()
            if rel not in _ALLOWED_VERTICAL_IMPORTS:
                offenders.append(f"{rel}: {', '.join(forbidden)}")

        dynamic_forbidden = [
            f"line {lineno}: {root}"
            for lineno, root in _dynamic_import_roots(path)
            if root in _FORBIDDEN_ENGINE_ROOTS
        ]
        if dynamic_forbidden:
            rel = path.relative_to(_BACKEND_ROOT).as_posix()
            if rel not in _ALLOWED_VERTICAL_IMPORTS:
                offenders.append(f"{rel}: {', '.join(dynamic_forbidden)}")

    assert offenders == [], (
        "pandas/geopandas imports must stay inside app/core/dataframes. "
        "Move engine-specific code behind a dataframe helper or port:\n"
        + "\n".join(offenders)
    )


def test_dataframe_ports_are_pandas_neutral() -> None:
    """The public dataframe contracts must stay engine-neutral."""
    ports_path = _DATAFRAME_BOUNDARY / "ports.py"
    imports = set(_import_roots(ports_path))
    assert not (_FORBIDDEN_ENGINE_ROOTS & imports)

    text = ports_path.read_text(encoding="utf-8")
    assert "FrameLike = Any" in text
    assert "GeoFrameLike = Any" in text
    assert "pd.DataFrame" not in text
    assert "gpd.GeoDataFrame" not in text
