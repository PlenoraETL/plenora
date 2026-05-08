"""Artifact resolver DB access must stay behind its named boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.architecture]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_ARTIFACTS_ROOT = _BACKEND_ROOT / "app" / "core" / "artifacts"
_RAW_DB_MODULES = {
    "app.core.projects.db",
    "app.core.results.db",
}
_RAW_DB_PACKAGES = {
    "app.core.projects",
    "app.core.results",
}


def _raw_db_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module in _RAW_DB_MODULES:
                imports.add(node.module)
            if node.module in _RAW_DB_PACKAGES:
                imports.update(
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                    if alias.name == "db"
                )
        elif isinstance(node, ast.Import):
            imports.update(
                alias.name
                for alias in node.names
                if alias.name in _RAW_DB_MODULES
            )

    return imports


def test_resolver_does_not_import_raw_project_or_result_db_modules() -> None:
    resolver_path = _ARTIFACTS_ROOT / "resolver.py"

    imported = _raw_db_imports(resolver_path)

    assert not imported, (
        "ArtifactResolver must use app.core.artifacts.db_boundary for "
        "project/result metadata reads instead of importing raw DB leaves. "
        f"Raw imports found: {sorted(imported)}"
    )


def test_artifact_raw_db_imports_are_isolated_to_named_boundary() -> None:
    offenders: list[str] = []

    for path in sorted(_ARTIFACTS_ROOT.rglob("*.py")):
        if path.name == "db_boundary.py":
            continue
        imported = _raw_db_imports(path)
        if imported:
            rel = path.relative_to(_BACKEND_ROOT).as_posix()
            offenders.append(f"{rel}: {sorted(imported)}")

    assert offenders == [], (
        "Raw app.core.projects.db/app.core.results.db imports in artifacts "
        "must remain isolated in app.core.artifacts.db_boundary. "
        f"Offenders: {offenders}"
    )
