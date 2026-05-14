"""Ratchet direct pandas/geopandas imports while dataframe engine migrates."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCAN_ROOTS = (
    _REPO_ROOT / "backend" / "app",
    _REPO_ROOT / "backend" / "scripts",
    _REPO_ROOT / "backend" / "workers",
)

_SCRIPT_SCAN_ROOTS = (
    _REPO_ROOT / "scripts",
)

_MAX_DIRECT_IMPORTS = 20

_ALLOWED_DIRECT_IMPORT_PATHS = {
    "backend/app/core/dataframes/default_loader.py",
    "backend/app/core/dataframes/frame_io.py",
    "backend/app/core/dataframes/geodata.py",
    "backend/app/core/dataframes/geospatial_io.py",
    "backend/app/core/dataframes/load_dispatch.py",
    "backend/app/core/dataframes/parallel_ops.py",
    "backend/app/core/dataframes/schema.py",
    "backend/app/core/dataframes/tabular.py",
    "backend/app/core/dataframes/values.py",
}

_PANDAS_SPECIFIC_RESOURCE_HELPERS = {
    "canvas_type_to_pandas_dtype",
    "pandas_series_to_canvas_type",
}

_DIRECT_DATAFRAME_IMPORT_RE = re.compile(
    r"\b(import\s+(?:pandas|geopandas)\b|from\s+(?:pandas|geopandas)\b)"
)


def _direct_dataframe_imports() -> list[tuple[str, int, str]]:
    imports: list[tuple[str, int, str]] = []
    for root in _SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            rel = path.relative_to(_REPO_ROOT).as_posix()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        base = alias.name.split(".")[0]
                        if base in {"pandas", "geopandas"}:
                            imports.append((rel, node.lineno, alias.name))
                elif isinstance(node, ast.ImportFrom) and node.module:
                    base = node.module.split(".")[0]
                    if base in {"pandas", "geopandas"}:
                        imports.append((rel, node.lineno, node.module))
    return sorted(imports)


def _direct_dataframe_imports_in_scripts() -> list[tuple[str, int, str]]:
    imports: list[tuple[str, int, str]] = []
    for root in _SCRIPT_SCAN_ROOTS:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".ps1", ".sh"}:
                continue
            rel = path.relative_to(_REPO_ROOT).as_posix()
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8-sig").splitlines(), start=1
            ):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                match = _DIRECT_DATAFRAME_IMPORT_RE.search(stripped)
                if match:
                    imports.append((rel, lineno, match.group(1)))
    return sorted(imports)


def test_direct_dataframe_imports_do_not_spread_to_new_files() -> None:
    imports = _direct_dataframe_imports()
    unknown_paths = sorted(
        {rel for rel, _line, _module in imports} - _ALLOWED_DIRECT_IMPORT_PATHS
    )

    assert not unknown_paths, (
        "Direct pandas/geopandas imports must not spread to new vertical files. "
        "Route new dataframe work through app.core.dataframes.* or reduce the "
        f"residual allowlist first. Unknown paths: {unknown_paths}"
    )


def test_direct_dataframe_imports_do_not_spread_to_runtime_scripts() -> None:
    imports = _direct_dataframe_imports_in_scripts()

    assert not imports, (
        "Repo runtime scripts must not import pandas/geopandas directly. "
        "Keep dataframe dependencies behind backend runtime boundaries; "
        f"violations: {imports}"
    )


def test_direct_dataframe_import_count_does_not_increase() -> None:
    imports = _direct_dataframe_imports()

    assert len(imports) <= _MAX_DIRECT_IMPORTS, (
        "Direct pandas/geopandas import statements increased during the "
        "dataframe engine migration. Move new behavior behind "
        f"app.core.dataframes.*. Observed={len(imports)} "
        f"budget={_MAX_DIRECT_IMPORTS}."
    )


def test_vertical_code_uses_dataframe_boundary_for_pandas_specific_type_helpers() -> None:
    violations: list[tuple[str, int, str]] = []
    for root in _SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(_REPO_ROOT).as_posix()
            if rel.startswith("backend/app/core/dataframes/"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "app.core.resources.column_types"
                ):
                    for alias in node.names:
                        if alias.name in _PANDAS_SPECIFIC_RESOURCE_HELPERS:
                            violations.append((rel, node.lineno, alias.name))

    assert not violations, (
        "Vertical code must not import pandas-specific type helpers from "
        "app.core.resources.column_types directly. Use "
        "app.core.dataframes.schema instead. Violations: "
        f"{violations}"
    )
