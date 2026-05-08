"""Guards for domain exception ownership."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable, Set, Tuple

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_ROOT = _BACKEND_ROOT / "app"

_CANONICAL_EXCEPTION_MODULES = {
    "app/core/projects/exceptions.py",
    "app/core/auth/exceptions.py",
    "app/core/intermediate/exceptions.py",
}

_DOMAIN_EXCEPTION_NAMES = {
    "AuthAccessDeniedError",
    "AuthDomainError",
    "AuthResourceNotFoundError",
    "CanvasCorruptedError",
    "IntermediateSettingsError",
    "IntermediateWriteError",
    "ProjectCleanupPartialError",
    "ProjectHasDependenciesError",
    "ProjectLineageCycleError",
}


def _rel(path: Path) -> str:
    return str(path.relative_to(_BACKEND_ROOT)).replace("\\", "/")


def _iter_py(root: Path) -> Iterable[Path]:
    yield from root.rglob("*.py")


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def _class_defs(path: Path) -> Set[str]:
    return {
        node.name
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.ClassDef)
    }


def _db_modules_for_domains() -> Iterable[Path]:
    for domain in ("projects", "auth", "intermediate"):
        domain_root = _APP_ROOT / "core" / domain
        for path in _iter_py(domain_root):
            if path.name == "exceptions.py":
                continue
            if path.name == "db.py" or path.name.endswith("_db.py"):
                yield path


def test_domain_exception_modules_exist():
    missing = [
        rel
        for rel in sorted(_CANONICAL_EXCEPTION_MODULES)
        if not (_BACKEND_ROOT / rel).exists()
    ]
    assert missing == []


def test_domain_exceptions_not_defined_in_shared_exceptions():
    shared = _APP_ROOT / "shared" / "exceptions.py"
    hits = _class_defs(shared) & _DOMAIN_EXCEPTION_NAMES
    assert hits == set(), (
        "Project/auth/intermediate domain exceptions must live in their "
        f"`core.<domain>.exceptions` module, not shared: {sorted(hits)}"
    )


def test_domain_exceptions_not_defined_in_db_modules():
    hits: Set[Tuple[str, str]] = set()
    for path in _db_modules_for_domains():
        for name in _class_defs(path) & _DOMAIN_EXCEPTION_NAMES:
            hits.add((_rel(path), name))

    assert hits == set(), (
        "DB modules must import domain exceptions from "
        f"`core.<domain>.exceptions` instead of defining them ad hoc: "
        f"{sorted(hits)}"
    )


def test_legacy_shared_imports_alias_canonical_domain_exceptions():
    from app.core.intermediate.exceptions import IntermediateWriteError
    from app.core.projects.exceptions import (
        CanvasCorruptedError,
        ProjectCleanupPartialError,
        ProjectHasDependenciesError,
    )
    from app.shared import exceptions as shared_exceptions

    assert shared_exceptions.CanvasCorruptedError is CanvasCorruptedError
    assert shared_exceptions.ProjectCleanupPartialError is ProjectCleanupPartialError
    assert shared_exceptions.ProjectHasDependenciesError is ProjectHasDependenciesError
    assert shared_exceptions.IntermediateWriteError is IntermediateWriteError
