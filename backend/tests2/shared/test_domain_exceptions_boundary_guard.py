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


def _production_modules() -> Iterable[Path]:
    for path in _iter_py(_APP_ROOT):
        if path == _APP_ROOT / "shared" / "exceptions.py":
            continue
        yield path


def _shared_exception_domain_imports(path: Path) -> Set[str]:
    hits: Set[str] = set()
    module_aliases: Set[str] = set()
    full_module_aliases: Set[Tuple[str, ...]] = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name != "app.shared.exceptions":
                    continue
                if alias.asname:
                    module_aliases.add(alias.asname)
                else:
                    full_module_aliases.add(("app", "shared", "exceptions"))
            continue

        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module == "app.shared":
            for alias in node.names:
                if alias.name == "exceptions":
                    module_aliases.add(alias.asname or alias.name)
            continue

        if node.module != "app.shared.exceptions":
            continue
        for alias in node.names:
            if alias.name in _DOMAIN_EXCEPTION_NAMES:
                hits.add(alias.name)

    for node in ast.walk(_parse(path)):
        if not isinstance(node, ast.Attribute):
            continue
        if node.attr not in _DOMAIN_EXCEPTION_NAMES:
            continue
        chain = _attribute_chain(node.value)
        if chain in full_module_aliases:
            hits.add(node.attr)
            continue
        if len(chain) == 1 and chain[0] in module_aliases:
            hits.add(node.attr)
    return hits


def _attribute_chain(node: ast.AST) -> Tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        return (*_attribute_chain(node.value), node.attr)
    return ()


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


def test_production_uses_domain_exception_modules_not_shared_compat():
    """Shared exception aliases are retained for tests and legacy importers.

    Runtime code must import moved project/auth/intermediate exceptions from
    the owning domain modules so `app.shared.exceptions` can keep shrinking.
    """
    hits: Set[Tuple[str, str]] = set()
    for path in _production_modules():
        for name in _shared_exception_domain_imports(path):
            hits.add((_rel(path), name))

    assert hits == set(), (
        "Production code must import moved domain exceptions from "
        "`app.core.<domain>.exceptions`, not `app.shared.exceptions`: "
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
