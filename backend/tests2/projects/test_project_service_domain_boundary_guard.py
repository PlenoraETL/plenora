from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROJECTS_CORE_ROOT = BACKEND_ROOT / "app" / "core" / "projects"
LEGACY_PROJECT_MODULES = {
    "app.services.project_service",
}


def _module_imports_legacy_project_boundary(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in LEGACY_PROJECT_MODULES:
            hits.append(node.module)
        if isinstance(node, ast.Import):
            hits.extend(
                alias.name
                for alias in node.names
                if alias.name in LEGACY_PROJECT_MODULES
            )
    return hits


def test_core_projects_do_not_import_legacy_project_service_wrappers() -> None:
    """Canonical project modules own this boundary; services wrappers are compat."""
    offenders: dict[str, list[str]] = {}
    for path in PROJECTS_CORE_ROOT.glob("*.py"):
        hits = _module_imports_legacy_project_boundary(path)
        if hits:
            rel = path.relative_to(BACKEND_ROOT).as_posix()
            offenders[rel] = hits

    assert offenders == {}


def test_legacy_project_service_wrappers_delegate_to_core_projects() -> None:
    legacy_root = BACKEND_ROOT / "app" / "services"
    expected_targets = {
        "project_service.py": "app.core.projects.service",
    }

    for filename, target_module in expected_targets.items():
        tree = ast.parse((legacy_root / filename).read_text(encoding="utf-8"))
        imports = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ]
        assert imports == [target_module]


def test_zero_consumer_project_wrappers_stay_removed() -> None:
    legacy_root = BACKEND_ROOT / "app" / "services"
    removed = {"project_domain.py", "project_repository.py"}

    assert {path.name for path in legacy_root.glob("project_*.py") if path.name in removed} == set()
